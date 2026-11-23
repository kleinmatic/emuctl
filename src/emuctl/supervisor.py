# SPDX-License-Identifier: GPL-2.0-only
"""Per-guest supervisor and console broker.

One supervisor runs one emulator. It holds the emulator's console serial (most
emulators will not boot unattended until a client is connected), types the
configured boot commands as their prompts appear, logs the console, re-serves
it to any number of clients on the broker port, and records what the guest is
doing in a state file.

Everything that touches the console runs on one thread, through pump(). A stop
request only sets a flag; the main loop then runs the stop sequence while it
keeps pumping, so the broker stays usable by a stop_command.

A guest with `console = none` (86Box draws a screen and has no serial
console) has no broker, stages or log; it is `ready` once the emulator has run
for `ready_after` seconds. A `display_command` (Xvnc, say) is started before
the emulator and stopped after it, and `display_wait` names a path, such as
the X server's socket, that must answer before the emulator starts.
"""
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from . import guard
from .state import write_state
from .telnet import TN_INIT, crlf_fix, strip_telnet

READY_WINDOW = 400      # bytes at the tail of the console that patterns may match in
CLIENT_TIMEOUT = 5      # seconds; a broker client slower than this is dropped
CONSOLE_POLL = 1        # seconds; console read timeout, the granularity of the main loop
CONSOLE_RETRY = 1       # seconds between attempts to connect the console
STOP_GRACE = 60         # seconds for the emulator to exit before SIGKILL
POKE_AFTER = 3          # seconds of total console silence before sending a CR
MONITOR_DRAIN = 10      # seconds to hold QEMU's monitor open after `quit`
DISPLAY_WAIT = 15       # seconds for display_wait to answer
HELPER_GRACE = 10       # seconds for the display helper to exit before SIGKILL


def path_answers(path):
    """True if `path` exists and, if it is a socket, accepts a connection: an
    X server's socket file appears before the server listens on it."""
    import stat
    try:
        if not stat.S_ISSOCK(os.stat(path).st_mode):
            return True
    except OSError:
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def log(msg):
    sys.stdout.write(f"[emuctl] {msg}\n")
    sys.stdout.flush()


def should_poke(has_boot_stages, saw_output, elapsed, after=POKE_AFTER):
    """Whether to send the CR that makes an idle console reprint its prompt.

    The poke is for a guest that printed its prompt before the supervisor
    attached. It is sent only if the console has been silent since attach: a
    guest that has printed anything is alive and booting, and a stray key
    there can abort firmware start-up (Open Firmware does). One byte of output
    vetoes the poke for good; pauses later in the boot must not re-arm it.
    """
    return bool(has_boot_stages) and not saw_output and elapsed >= after


class StageDriver:
    """Yield configured commands as their prompts appear, in order, once each."""

    def __init__(self, stages):
        self._stages = list(stages)
        self._i = 0
        self._buf = b""

    @property
    def done(self):
        return self._i >= len(self._stages)

    def feed(self, chunk):
        if self.done:
            return None
        self._buf = (self._buf + chunk.replace(b"\x00", b""))[-4096:]
        stage = self._stages[self._i]
        if stage.prompt in self._buf:
            self._i += 1
            self._buf = b""
            return stage.command
        return None


VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand(cmd, env):
    """Replace each ${NAME} in a stage command with env[NAME].

    This lets a stage type a password without the password being in the conf:
    the value comes from the supervisor's environment, which the service
    manager fills from a root-only file. Raises KeyError for an unset name.
    """
    return VAR_RE.sub(lambda m: env[m.group(1)], cmd)


def missing_vars(stages, env):
    """Names used as ${NAME} in `stages` that `env` does not set."""
    return sorted({name for st in stages for name in VAR_RE.findall(st.command)
                   if name not in env})


def match_tail(buf, pattern):
    """True if regex `pattern` matches near the tail of `buf`, so text from
    earlier in the scrollback cannot satisfy it.

    When the window cuts a line in half, that partial first line is dropped:
    its tail could match a pattern it does not really contain, and `^` would
    match at the cut.
    """
    clean = buf.replace(b"\x00", b"")
    tail = clean[-READY_WINDOW:]
    if len(clean) > READY_WINDOW and clean[-READY_WINDOW - 1:-READY_WINDOW] != b"\n":
        nl = tail.find(b"\n")
        if nl >= 0:                         # with no newline at all, keep the lot
            tail = tail[nl + 1:]
    return re.search(pattern, tail.decode("latin-1")) is not None


def match_stuck(buf, patterns):
    """The first of `patterns` that matches near the tail of `buf`, or None."""
    for pattern in patterns:
        if match_tail(buf, pattern):
            return pattern
    return None


class Supervisor:
    def __init__(self, cfg, settings):
        self.cfg = cfg
        self.settings = settings
        self.proc = None
        self.up = None                      # console connection to the emulator
        self.clients = []
        self.clk = threading.Lock()
        self.stop_requests = 0              # 1 = stop, 2+ = stop without waiting
        self.done = threading.Event()       # tells the helper threads to exit
        self.state = "starting"
        self.started_at = int(time.time())
        self.log_fh = None
        self.buf = b""
        self.ready = False
        self.halted = False
        self.driver = StageDriver(cfg.boot_stages)
        self.stage_env = dict(os.environ, **cfg.env)   # for ${NAME} in stage commands
        self.attached_at = None
        self.saw_output = False
        self.poked = False
        self.boot_deadline = None
        self.exiting = False                # the emulator has been told to exit
        self.command_halted = False         # a stop_command with no halted pattern exited 0
        self.helper = None                  # the display_command process

    # --- state -----------------------------------------------------------
    def set_state(self, state, **extra):
        self.state = state
        write_state(self.settings.run_dir, self.cfg.name, state=state,
                    pid=(self.proc.pid if self.proc and state != "down" else None),
                    cmd=" ".join(self.cfg.command), supervisor_pid=os.getpid(),
                    node=self.cfg.node, broker=self.cfg.broker,
                    started_at=self.started_at, stop_now=True, **extra)

    def running(self):
        return self.proc.poll() is None

    def request_stop(self, signum=None, frame=None):
        """Signal handler. A second request skips the in-guest shutdown."""
        self.stop_requests += 1

    def request_stop_now(self, signum=None, frame=None):
        """SIGUSR2 handler (`emuctl stop --now`): stop, skipping the in-guest
        shutdown or ending one under way. Counts as a second request."""
        self.stop_requests = max(self.stop_requests + 1, 2)

    # --- broker ----------------------------------------------------------
    def broadcast(self, data):
        """Send to every broker client without holding the lock across the
        writes, so one client that stops reading cannot stall the console."""
        with self.clk:
            targets = list(self.clients)
        dead = []
        for c in targets:
            try:
                c.sendall(data)
            except Exception:
                dead.append(c)
        if dead:
            with self.clk:
                self.clients = [c for c in self.clients if c not in dead]
            for c in dead:
                try:
                    c.close()
                except Exception:
                    pass

    def note(self, text):
        """A supervisor remark, written into the console log and to clients.
        (A guest with no console has neither; its remarks go to the journal.)"""
        data = f"\r\n[emuctl] {text}\r\n".encode()
        if self.log_fh:
            self.log_fh.write(data)
        self.broadcast(data)

    def send_console(self, data):
        up = self.up
        if not up:
            return False
        try:
            up.sendall(data)
            return True
        except OSError:
            return False                    # being reattached; the bytes are dropped

    def client_reader(self, sock):
        st, pend = 0, False
        try:
            while not self.done.is_set():
                try:
                    d = sock.recv(4096)
                except socket.timeout:
                    continue                # an idle client is normal
                if not d:
                    break
                clean, st = strip_telnet(d, st)
                clean, pend = crlf_fix(clean, pend)
                if clean:
                    self.send_console(clean)
        except Exception:
            pass
        finally:
            with self.clk:
                if sock in self.clients:
                    self.clients.remove(sock)
            try:
                sock.close()
            except Exception:
                pass

    def listener(self, ls):
        ls.settimeout(1)
        while not self.done.is_set():
            try:
                c, _ = ls.accept()
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.5)
                continue
            c.settimeout(CLIENT_TIMEOUT)
            with self.clk:
                self.clients.append(c)
            try:
                c.sendall(TN_INIT)
                c.sendall(f"\r\n[emuctl] {self.cfg.name} console -- shared; "
                          f"others may be attached\r\n".encode())
            except Exception:
                pass
            threading.Thread(target=self.client_reader, args=(c,), daemon=True).start()

    def hold(self, port):
        """Keep a client on a serial port that is not the console. Some
        emulators wait for every configured serial before starting."""
        while self.running() and not self.done.is_set():
            try:
                s = socket.create_connection((self.cfg.bind, port), timeout=5)
                s.settimeout(10)
                while self.running() and not self.done.is_set():
                    try:
                        if not s.recv(4096):
                            break
                    except socket.timeout:
                        pass
                s.close()
            except Exception:
                time.sleep(CONSOLE_RETRY)

    # --- console ---------------------------------------------------------
    def open_console(self, seconds, give_up_at=2):
        """Connect to the console serial; None if it does not answer in time
        or `give_up_at` stop requests have arrived."""
        deadline = time.time() + seconds
        while (time.time() < deadline and self.running()
               and self.stop_requests < give_up_at):
            try:
                s = socket.create_connection(
                    (self.cfg.bind, self.cfg.serials[0]), timeout=5)
                s.settimeout(CONSOLE_POLL)
                return s
            except Exception:
                time.sleep(CONSOLE_RETRY)
        return None

    def reattach_console(self):
        """The console connection closed while the emulator is still running.

        An emulator may drop a console client and keep going (es40 does when
        the client's output queue fills; SIMH waits for a new client). Without
        reattaching, the log, the broker and the state would all stop while the
        guest carries on. Returns False once the emulator has exited.
        """
        lost_at = time.time()
        old, self.up = self.up, None
        try:
            old.close()
        except Exception:
            pass
        log("console connection closed; reattaching")
        while self.running() and self.stop_requests < 2:
            s = self.open_console(30)
            if s:
                self.up = s
                log(f"console reattached after {int(time.time() - lost_at)}s")
                self.note("console connection was lost and reattached; "
                          "output in between is missing")
                return True
        return False

    def pump(self):
        """One console read and everything that follows from it. Returns False
        when the console is gone and will not come back."""
        if not self.up:
            time.sleep(CONSOLE_POLL)
            return self.running()
        lost = False
        try:
            d = self.up.recv(4096)
            lost = not d
        except socket.timeout:
            d = b""
        except Exception:
            lost = True
        if lost and self.exiting:
            self.up = None                  # expected: the emulator is going away
            return self.running()
        if lost:
            return self.reattach_console()

        if not d:
            if not self.poked and should_poke(self.cfg.boot_stages, self.saw_output,
                                              time.time() - self.attached_at):
                self.poked = True
                log("console silent since attach; sending a CR")
                self.send_console(b"\r")
        else:
            self.saw_output = True
            self.log_fh.write(d)
            self.broadcast(d)
            self.buf = (self.buf + d)[-8000:]
            cmd = self.driver.feed(d)
            if cmd:
                time.sleep(1)
                # Log the command as written in the conf, never its expansion.
                try:
                    self.send_console(expand(cmd, self.stage_env).encode() + b"\r")
                    self.note(f"sent: {cmd}")
                except KeyError as exc:
                    log(f"stage not sent: {cmd!r} uses {exc}, which is not set")
            if self.state not in ("stopping",):
                self.judge()

        if (self.boot_deadline and not self.ready and not self.halted
                and time.time() > self.boot_deadline):
            log(f"not ready after {self.cfg.boot_timeout}s; still {self.state}")
            self.boot_deadline = None       # say it once
        return True

    def judge(self):
        """Derive ready / stuck / halted from the console tail."""
        cfg = self.cfg
        if not self.ready and self.driver.done and match_tail(self.buf, cfg.ready):
            self.ready = True
            self.halted = False
            self.buf = b""                  # `halted` must be printed after this point
            self.set_state("ready", ready_at=int(time.time()))
            log("guest is ready")
        elif not self.ready and cfg.stuck:
            hit = match_stuck(self.buf, cfg.stuck)
            if hit and self.state != "stuck":
                self.set_state("stuck", stuck_on=hit)
                log(f"boot is waiting at a prompt matching {hit!r}")
            elif not hit and self.state == "stuck":
                self.set_state("booting")
                log("boot moved on")
        # A guest shut down from inside may leave the emulator running at its
        # firmware prompt. That is `halted`: safe to stop, not ready.
        if cfg.halted and self.ready and match_tail(self.buf, cfg.halted):
            self.ready = False
            self.halted = True
            self.buf = b""
            self.set_state("halted", halted_at=int(time.time()))
            log("guest halted; the emulator is still running")

    def pump_until(self, deadline, done=lambda: False, hurry=True):
        """Pump until `done()`, the deadline or emulator exit; with `hurry`,
        also until a second stop request. True only if `done()` became true."""
        while self.running() and time.time() < deadline:
            if done():
                return True
            if hurry and self.stop_requests > 1:
                return False
            if not self.pump():
                break
        return done()

    # --- stop ------------------------------------------------------------
    def guest_is_down(self):
        return bool(self.cfg.halted) and match_tail(self.buf, self.cfg.halted)

    def shutdown(self):
        """Stop the guest if the conf says how, then stop the emulator."""
        cfg = self.cfg
        was = self.state
        self.set_state("stopping")
        deadline = time.time() + cfg.stop_timeout

        if self.stop_requests > 1 and was in ("ready", "booting", "stuck"):
            log("stop now requested: skipping the in-guest shutdown")
        elif was == "ready" and cfg.stop_command:
            self.run_stop_command(deadline)
        elif was == "ready" and cfg.stop_stages:
            self.run_stop_stages(deadline)
        elif was not in ("ready", "halted"):
            log(f"guest was {was}, not ready; no in-guest shutdown attempted")

        if not self.running():
            return
        log(f"stopping the emulator via {cfg.stop_mode}")
        if (cfg.stop_mode == "signal" and was != "halted" and not self.guest_is_down()
                and not self.command_halted):
            log("NOTE: the guest OS was not shut down first; for it this is a power cut")
        self.exiting = cfg.stop_mode != "freeze"
        try:
            if cfg.stop_mode == "monitor":
                self.monitor_quit()
            elif cfg.stop_mode == "freeze":
                self.freeze()
            else:
                self.proc.send_signal(getattr(signal, f"SIG{cfg.stop_signal}"))
        except Exception as exc:
            log(f"stop primitive failed: {exc}")

        self.pump_until(time.time() + STOP_GRACE, hurry=False)
        if self.running():
            log("emulator did not exit; SIGKILL")
            self.proc.kill()
        self.proc.wait()

    def run_stop_command(self, deadline):
        cfg = self.cfg
        env = dict(os.environ, **cfg.env)
        env.update(EMUCTL_GUEST=cfg.name, EMUCTL_NODE=cfg.node, EMUCTL_WORKDIR=cfg.workdir,
                   EMUCTL_BROKER_HOST=cfg.bind, EMUCTL_BROKER_PORT=str(cfg.broker),
                   EMUCTL_GUEST_ADDR=cfg.guest_addr or "")
        log(f"running stop_command: {' '.join(cfg.stop_command)}")
        try:
            hook = subprocess.Popen(cfg.stop_command, env=env, cwd=cfg.workdir,
                                    stdin=subprocess.DEVNULL)
        except OSError as exc:
            log(f"stop_command could not start: {exc}")
            return
        if not self.pump_until(deadline, lambda: hook.poll() is not None):
            log("stop now requested: abandoning stop_command" if self.stop_requests > 1
                else "stop_command did not finish in time; killing it")
            hook.kill()
            hook.wait()
            return
        log(f"stop_command exited {hook.returncode}")
        if hook.returncode == 0 and not cfg.halted:
            # Nothing on the console to confirm it, so exit 0 is the command
            # saying the guest is down.
            self.command_halted = True
        elif hook.returncode == 0:
            if self.pump_until(deadline, self.guest_is_down):
                log("guest reached its halted prompt")
                self.grace()
            elif self.running():
                log("stop now requested: not waiting for the halted prompt"
                    if self.stop_requests > 1 else
                    "guest did not reach its halted prompt in time")

    def run_stop_stages(self, deadline):
        cfg = self.cfg
        missing = missing_vars(cfg.stop_stages, self.stage_env)
        if missing:
            log(f"the stop sequence needs {', '.join(missing)}, which the supervisor's "
                "environment does not set; skipping it")
            return
        log(f"typing the stop sequence ({len(cfg.stop_stages)} stage(s))")
        self.driver = StageDriver(cfg.stop_stages)
        self.buf = b""
        if cfg.stop_wake:
            self.send_console(b"\r")
        if not self.pump_until(deadline, lambda: self.driver.done):
            log("stop now requested: abandoning the stop sequence" if self.stop_requests > 1
                else "stop sequence did not complete in time")
            return
        if cfg.halted:
            if self.pump_until(deadline, self.guest_is_down):
                log("guest reached its halted prompt")
                self.grace()
            elif self.running():
                log("stop now requested: not waiting for the halted prompt"
                    if self.stop_requests > 1 else
                    "guest did not reach its halted prompt in time")

    def grace(self):
        """Wait halt_grace seconds after the halted pattern, for a guest whose
        last console line comes a little before it has finished writing."""
        if self.cfg.halt_grace:
            log(f"waiting {self.cfg.halt_grace}s (halt_grace)")
            self.pump_until(time.time() + self.cfg.halt_grace)

    def monitor_quit(self):
        """Send `quit` on QEMU's human monitor and hold the connection until
        QEMU hangs up.

        The monitor is a readline interface and runs a line only once its
        reader has processed the newline. Closing the socket straight after
        the write can lose that race on older QEMU builds: the chardev is torn
        down first and `quit` is discarded. Reading to EOF waits exactly as
        long as needed and confirms the exit. Bounded by MONITOR_DRAIN.
        """
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(10)
            s.connect(self.cfg.monitor_sock)
            s.sendall(b"quit\n")
            s.settimeout(1)
            deadline = time.time() + MONITOR_DRAIN
            while time.time() < deadline:
                try:
                    if not s.recv(4096):
                        break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            s.close()

    def freeze(self):
        """SIMH: Ctrl-E to reach `sim>`, save the machine state, quit."""
        if not self.up:
            raise RuntimeError("no console connection for freeze")
        state_file = os.path.join(self.cfg.workdir, "frozen.state")
        self.send_console(b"\x05")
        self.pump_until(time.time() + 2, hurry=False)
        self.send_console(f"save {state_file}\r".encode())
        self.pump_until(time.time() + 10, hurry=False)
        self.send_console(b"quit\r")
        self.exiting = True

    def finalize(self):
        rc = self.proc.poll()
        if self.stop_requests or rc == 0:
            # Exit 0 without a stop request is a guest shut down from inside
            # (SIMH exits when its guest halts). That is `down`, not a crash.
            if not self.stop_requests:
                log("emulator exited 0; the guest was shut down from inside")
            self.set_state("down", exit_code=rc)
        else:
            log(f"emulator exited {rc}")
            self.set_state("crashed", exit_code=rc)

    # --- display helper --------------------------------------------------
    def start_helper(self, out):
        """Start display_command and wait for display_wait. Returns an error
        message, or None when the emulator may start."""
        cfg = self.cfg
        if not cfg.display_command:
            return None
        log(f"starting the display helper: {' '.join(cfg.display_command)}")
        try:
            self.helper = subprocess.Popen(cfg.display_command, env=dict(os.environ, **cfg.env),
                                           cwd=cfg.workdir, stdout=out,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except OSError as exc:
            return f"cannot start the display helper {cfg.display_command[0]}: {exc}"
        if not cfg.display_wait:
            return None
        deadline = time.time() + DISPLAY_WAIT
        while time.time() < deadline and not self.stop_requests:
            if self.helper.poll() is not None:
                return f"the display helper exited {self.helper.returncode} before " \
                       f"{cfg.display_wait} answered"
            if path_answers(cfg.display_wait):
                return None
            time.sleep(0.1)
        if self.stop_requests:
            return "stop requested while waiting for the display"
        return f"{cfg.display_wait} did not answer within {DISPLAY_WAIT}s"

    def stop_helper(self):
        h, self.helper = self.helper, None
        if not h or h.poll() is not None:
            return
        log("stopping the display helper")
        h.terminate()
        try:
            h.wait(HELPER_GRACE)
        except subprocess.TimeoutExpired:
            h.kill()
            h.wait()

    def run_without_console(self):
        """The main loop for a guest with no console: ready after ready_after
        seconds, until a stop is asked for or the emulator exits."""
        self.set_state("booting")
        ready_at = time.time() + self.cfg.ready_after
        while self.running():
            if self.stop_requests:
                self.shutdown()
                break
            if not self.ready and time.time() >= ready_at:
                self.ready = True
                self.set_state("ready", ready_at=int(time.time()))
                log("guest is ready (the emulator has run for ready_after seconds)")
            time.sleep(min(CONSOLE_POLL, 0.25))

    # --- run -------------------------------------------------------------
    def run(self):
        cfg = self.cfg
        try:
            locks = guard.check_and_lock(cfg, self.settings)
        except guard.GuardError as exc:
            log(f"REFUSING to start {cfg.name}: {exc}")
            return 2

        ls = None
        if cfg.has_console:
            ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ls.bind((cfg.bind, cfg.broker))
                ls.listen(8)
            except OSError as exc:
                log(f"cannot bind the broker on {cfg.bind}:{cfg.broker}: {exc}")
                guard.release(locks)
                return 1

        self.started_at = int(time.time())
        for kind, stages in (("boot", cfg.boot_stages), ("stop", cfg.stop_stages)):
            missing = missing_vars(stages, self.stage_env)
            if missing:
                log(f"WARNING: {kind} stages use {', '.join(missing)}, which is not set")
        out = open(os.path.join(cfg.workdir, f"{cfg.name}.out"), "ab")
        if cfg.has_console:
            self.log_fh = open(os.path.join(cfg.workdir, "console.log"), "ab", buffering=0)
        error = self.start_helper(out)
        if error:
            log(error)
            self.stop_helper()
            if ls:
                ls.close()
            guard.release(locks)
            stopped = bool(self.stop_requests)
            write_state(self.settings.run_dir, cfg.name, state="down" if stopped else "crashed",
                        error=None if stopped else error, node=cfg.node,
                        started_at=self.started_at)
            return 0 if stopped else 1
        try:
            self.proc = subprocess.Popen(cfg.command, env=dict(os.environ, **cfg.env),
                                         cwd=cfg.workdir, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
        except OSError as exc:
            log(f"cannot start {cfg.command[0]}: {exc}")
            self.stop_helper()
            if ls:
                ls.close()
            guard.release(locks)
            return 1
        self.set_state("starting")
        if ls:
            threading.Thread(target=self.listener, args=(ls,), daemon=True).start()

        try:
            if not cfg.has_console:
                self.run_without_console()
                self.proc.wait()
                self.finalize()
                return 0

            self.up = self.open_console(cfg.console_timeout, give_up_at=1)
            if not self.up:
                if self.stop_requests:
                    log("stop requested before the console answered")
                    self.proc.terminate()
                    try:
                        self.proc.wait(STOP_GRACE)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                    self.proc.wait()
                    self.set_state("down", exit_code=self.proc.returncode)
                    return 0
                if self.running():
                    log(f"console on {cfg.bind}:{cfg.serials[0]} never answered; "
                        f"stopping the emulator")
                    self.proc.kill()
                self.proc.wait()
                self.set_state("crashed", exit_code=self.proc.returncode,
                               error="console never answered")
                return 1

            for port in cfg.serials[1:]:
                threading.Thread(target=self.hold, args=(port,), daemon=True).start()
            self.attached_at = time.time()
            self.boot_deadline = time.time() + cfg.boot_timeout
            self.set_state("booting")

            while self.running():
                if self.stop_requests:
                    self.shutdown()
                    break
                if not self.pump():
                    break
            if self.running() and self.stop_requests:
                # pump() gave up (a console reattach abandoned by stop-now)
                # with the emulator still up: stop it rather than wait forever.
                self.shutdown()
            self.proc.wait()
            self.finalize()
            return 0
        finally:
            self.done.set()
            self.stop_helper()              # after the emulator, which draws on it
            if ls:
                ls.close()
            guard.release(locks)


def main(cfg, settings):
    sup = Supervisor(cfg, settings)
    signal.signal(signal.SIGTERM, sup.request_stop)
    signal.signal(signal.SIGINT, sup.request_stop)
    signal.signal(signal.SIGUSR2, sup.request_stop_now)
    return sup.run()
