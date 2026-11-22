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


def match_tail(buf, pattern):
    """True if regex `pattern` matches near the tail of `buf`, so text from
    earlier in the scrollback cannot satisfy it."""
    tail = buf.replace(b"\x00", b"")[-READY_WINDOW:]
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
        self.attached_at = None
        self.saw_output = False
        self.poked = False
        self.boot_deadline = None
        self.exiting = False                # the emulator has been told to exit

    # --- state -----------------------------------------------------------
    def set_state(self, state, **extra):
        self.state = state
        write_state(self.settings.run_dir, self.cfg.name, state=state,
                    pid=(self.proc.pid if self.proc and state != "down" else None),
                    cmd=" ".join(self.cfg.command), supervisor_pid=os.getpid(),
                    node=self.cfg.node, broker=self.cfg.broker,
                    started_at=self.started_at, **extra)

    def running(self):
        return self.proc.poll() is None

    def request_stop(self, signum=None, frame=None):
        """Signal handler. A second request skips the in-guest shutdown."""
        self.stop_requests += 1

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
        """A supervisor remark, written into the console log and to clients."""
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
                self.send_console(cmd.encode() + b"\r")
                self.note(f"sent: {cmd}")
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

        if was == "ready" and cfg.stop_command:
            self.run_stop_command(deadline)
        elif was == "ready" and cfg.stop_stages:
            self.run_stop_stages(deadline)
        elif was not in ("ready", "halted"):
            log(f"guest was {was}, not ready; no in-guest shutdown attempted")

        if not self.running():
            return
        log(f"stopping the emulator via {cfg.stop_mode}")
        if cfg.stop_mode == "signal" and was != "halted" and not self.guest_is_down():
            log("NOTE: the guest OS was not shut down first; for it this is a power cut")
        self.exiting = cfg.stop_mode != "freeze"
        try:
            if cfg.stop_mode == "monitor":
                self.monitor_quit()
            elif cfg.stop_mode == "freeze":
                self.freeze()
            else:
                self.proc.terminate()
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
            log("stop_command did not finish in time; killing it")
            hook.kill()
            hook.wait()
            return
        log(f"stop_command exited {hook.returncode}")
        if hook.returncode == 0 and cfg.halted:
            if self.pump_until(deadline, self.guest_is_down):
                log("guest reached its halted prompt")
            elif self.running():
                log("guest did not reach its halted prompt in time")

    def run_stop_stages(self, deadline):
        cfg = self.cfg
        log(f"typing the stop sequence ({len(cfg.stop_stages)} stage(s))")
        self.driver = StageDriver(cfg.stop_stages)
        self.buf = b""
        if cfg.stop_wake:
            self.send_console(b"\r")
        if not self.pump_until(deadline, lambda: self.driver.done):
            log("stop sequence did not complete in time")
            return
        if cfg.halted:
            if self.pump_until(deadline, self.guest_is_down):
                log("guest reached its halted prompt")
            elif self.running():
                log("guest did not reach its halted prompt in time")

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

    # --- run -------------------------------------------------------------
    def run(self):
        cfg = self.cfg
        try:
            locks = guard.check_and_lock(cfg, self.settings)
        except guard.GuardError as exc:
            log(f"REFUSING to start {cfg.name}: {exc}")
            return 2

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
        out = open(os.path.join(cfg.workdir, f"{cfg.name}.out"), "ab")
        self.log_fh = open(os.path.join(cfg.workdir, "console.log"), "ab", buffering=0)
        try:
            self.proc = subprocess.Popen(cfg.command, env=dict(os.environ, **cfg.env),
                                         cwd=cfg.workdir, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
        except OSError as exc:
            log(f"cannot start {cfg.command[0]}: {exc}")
            guard.release(locks)
            return 1
        self.set_state("starting")
        threading.Thread(target=self.listener, args=(ls,), daemon=True).start()

        try:
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
            self.proc.wait()
            self.finalize()
            return 0
        finally:
            self.done.set()
            ls.close()
            guard.release(locks)


def main(cfg, settings):
    sup = Supervisor(cfg, settings)
    signal.signal(signal.SIGTERM, sup.request_stop)
    signal.signal(signal.SIGINT, sup.request_stop)
    return sup.run()
