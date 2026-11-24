# SPDX-License-Identifier: GPL-2.0-only
"""What machine a guest is: read from the emulator's own configuration.

One parser per emulator kind. Each takes the config text and returns the same
shape, leaving out anything it does not understand. None of them raises: an
emulator config is the user's file, written for the emulator, not for us.

    {"machine": str|None, "cpu": str|None, "memory": str|None,
     "disks": [{"unit", "model", "file", "kind", "readonly"}],
     "nics":  [{"unit", "model", "mac", "host_if"}],
     "other": [str], "sources": [path], "problems": [str],
     "forwards": [{"host_port", "guest_port"}]}   # TCP ports the emulator forwards

`hw.<label>` lines in the guest conf take precedence over anything parsed.
"""
import json
import os
import re
import shlex
import stat

TAPE_PREFIXES = ("tq", "ts", "tu", "tm", "mt", "ta")
NIC_PREFIXES = ("xq", "xu", "ni", "ec")
# `set <unit> <word>` words that are switches, not a drive model.
SET_FLAGS = {"enable", "disable", "online", "offline", "enabled", "disabled", "writeenabled",
             "locked", "write", "nowrite", "autosize", "noautosize", "debug", "nodebug",
             "buffered", "nobuffer", "nonbuffered"}
SIMH_MACHINES = {
    "vax": "MicroVAX 3900", "microvax3900": "MicroVAX 3900", "microvax3100": "MicroVAX 3100",
    "microvax2": "MicroVAX II", "microvax1": "MicroVAX I", "vax730": "VAX-11/730",
    "vax750": "VAX-11/750", "vax780": "VAX-11/780", "vax8200": "VAX 8200", "vax8600": "VAX 8600",
    "pdp11": "PDP-11", "pdp10": "PDP-10", "pdp8": "PDP-8", "pdp1": "PDP-1",
}
GXEMUL_MODELS = {"3max": "DECstation 5000/200", "3min": "DECstation 5000/1xx",
                 "pmax": "DECstation 3100", "3maxplus": "DECstation 5000/240",
                 "maxine": "Personal DECstation 5000/xx"}


def empty():
    return {"machine": None, "cpu": None, "memory": None, "disks": [], "nics": [],
            "other": [], "sources": [], "problems": [], "forwards": []}


def mb(n):
    """'128 MB', or '2 GB' for a whole number of gigabytes."""
    n = int(n)
    return f"{n // 1024} GB" if n >= 1024 and n % 1024 == 0 else f"{n} MB"


def size_to_mb(text):
    """'64m' -> 64, '1G' -> 1024, '192' -> 192 (QEMU's default unit is MB)."""
    m = re.fullmatch(r"(\d{1,12})\s*([kKmMgG]?)[bB]?", text.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n // 1024 if unit == "k" else n * 1024 if unit == "g" else n


# --- SIMH --------------------------------------------------------------------

def parse_simh(text, program=None):
    hw = empty()
    prog = os.path.basename(program or "").lower()
    hw["machine"] = SIMH_MACHINES.get(prog) or (prog or None)
    disks, nics = {}, {}

    def disk(unit):
        return disks.setdefault(unit, {"unit": unit, "model": None, "file": None,
                                       "kind": "tape" if unit.startswith(TAPE_PREFIXES)
                                       else "disk", "readonly": False})

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        try:
            words = shlex.split(line, comments=False)
        except ValueError:
            words = line.split()
        if len(words) < 2:
            continue
        verb, dev = words[0].lower(), words[1].lower()
        args = words[2:]
        if verb in ("set", "se") and dev == "cpu" and args:
            for opt in ",".join(args).split(","):
                opt = opt.strip()
                if size_to_mb(opt) and opt[-1:].lower() in ("m", "k", "g"):
                    hw["memory"] = mb(size_to_mb(opt))
                elif opt.lower().startswith("model="):
                    model = opt.split("=", 1)[1]
                    hw["machine"] = f"VAX {model}" if model.isdigit() else model
        elif verb in ("set", "se") and args:
            opts = ",".join(args).split(",")
            if dev.startswith(NIC_PREFIXES) or any(
                    o.lower().startswith("mac=") for o in opts):
                nic = nics.setdefault(dev, {"unit": dev, "model": dev.upper(), "mac": None,
                                            "host_if": None})
                for o in opts:
                    k, _, v = o.partition("=")
                    if k.lower() == "mac":
                        nic["mac"] = v.lower().replace("-", ":")
                    elif k.lower() == "type":
                        nic["model"] = v.upper()
            elif re.fullmatch(r"[a-z]+\d+", dev):
                first = opts[0].lower()
                if first in SET_FLAGS or "=" in first:
                    continue
                d = disk(dev)
                if first == "cdrom" or first.startswith(("rrd", "rrw")):
                    d["kind"] = "cdrom"
                d["model"] = "CD-ROM" if first == "cdrom" else first.upper()
        elif verb in ("attach", "att", "at"):
            switches = [w.lower() for w in words[1:] if w.startswith("-")]
            rest = [w for w in words[1:] if not w.startswith("-")]
            if "-f" in switches and len(rest) >= 3:
                rest = [rest[0], rest[2]]           # attach -f <unit> <format> <file>
            if len(rest) < 2:
                continue
            unit, target = rest[0].lower(), rest[1]
            if target.lower().startswith(("tap:", "eth:", "vde:", "nat:", "udp:", "pcap:")) \
                    or unit.startswith(NIC_PREFIXES):
                if ":" not in target:
                    target = "eth:" + target            # attach xq eth0: a host NIC by name
                nic = nics.setdefault(unit, {"unit": unit, "model": unit.upper(), "mac": None,
                                             "host_if": None})
                scheme, _, name = target.partition(":")
                if scheme.lower() in ("tap", "eth", "vde", "pcap"):
                    nic["host_if"] = name
                else:
                    hw["other"].append(f"{unit}: {scheme.lower()} networking")
            elif re.fullmatch(r"[a-z]+\d+", unit) and unit not in ("nvr0",):
                d = disk(unit)
                d["file"] = target
                d["readonly"] = "-r" in switches
                if target.lower().endswith(".iso"):
                    d["kind"] = "cdrom"
            elif unit in ("dz", "vh", "dhu", "dl") and target.isdigit():
                hw["other"].append(f"{unit.upper()} terminal lines on port {target}")
    hw["disks"] = [d for d in disks.values() if d["file"] or d["model"]]
    hw["nics"] = list(nics.values())
    return hw


# --- es40 --------------------------------------------------------------------

# A block body: no braces inside, and bounded so a malformed file cannot make
# the regexes rescan to the end of the text for every opening brace.
BODY = r"([^{}]{0,4000})"


def _props(body):
    return {k: v.strip().strip('"') for k, v in re.findall(r"([\w.]+)\s*=\s*([^;]+);", body)}


def parse_es40(text):
    hw = empty()
    # Drop // and # comments, but not inside a quoted path ("/a//b#1.img").
    text = re.sub(r'("[^"\n]*")|//[^\n]*|#[^\n]*', lambda m: m.group(1) or "", text)
    if re.search(r"=\s*tsunami\b", text):
        hw["machine"] = "AlphaServer ES40"
    m = re.search(r"memory\.bits\s*=\s*(\d{1,3})\b", text)
    if m and 20 <= int(m.group(1)) <= 40:
        hw["memory"] = mb(2 ** int(m.group(1)) // (1024 * 1024))
    cpus = re.findall(r"\bcpu\d{1,3}\s*=\s*(\w+)\s*(?:\{" + BODY + r"\})?", text)
    if cpus:
        model = cpus[0][0].upper()
        speed = _props(cpus[0][1] or "").get("speed", "")
        sm = re.fullmatch(r"(\d{1,6})\s*([MG])", speed.upper())
        mhz = (int(sm.group(1)) * (1000 if sm.group(2) == "G" else 1)) if sm else None
        hw["cpu"] = f"{len(cpus)} × {model}" + (f", {mhz} MHz" if mhz else "")
    for unit, dtype, body in re.findall(r"\b(disk\d{1,3}\.\d{1,3})\s*=\s*(\w+)\s*\{" + BODY + r"\}",
                                        text):
        p = _props(body)
        cdrom = p.get("cdrom", "").lower() == "true"
        hw["disks"].append({"unit": unit, "model": dtype, "file": p.get("file"),
                            "kind": "cdrom" if cdrom else "disk",
                            "readonly": cdrom or p.get("read_only", "").lower() == "true"})
    for unit, model, body in re.findall(r"\b(pci\d{1,3}\.\d{1,3})\s*=\s*(dec21143|e1000)\s*\{"
                                        + BODY + r"\}", text):
        p = _props(body)
        adapter = p.get("adapter")
        hw["nics"].append({"unit": unit,
                           "model": "DEC 21143" if model == "dec21143" else model.upper(),
                           "mac": p["mac"].lower().replace("-", ":") if p.get("mac") else None,
                           "host_if": os.path.basename(adapter) if adapter else None})
    for unit, body in re.findall(r"\b(serial\d{1,3})\s*=\s*serial\s*\{" + BODY + r"\}", text):
        port = _props(body).get("port")
        if port:
            hw["other"].append(f"{unit} on port {port}")
    return hw


# --- QEMU ----------------------------------------------------------------------

def _kv_opts(text):
    out, first = {}, None
    for i, part in enumerate(text.split(",")):
        k, eq, v = part.partition("=")
        if not eq and i == 0:
            first = k
        else:
            out[k] = v
    return first, out


def _forwards(hw, val):
    """QEMU user networking's hostfwd=tcp:[addr]:HOSTPORT-[addr]:GUESTPORT."""
    for m in re.finditer(r"hostfwd=tcp:[^:,]{0,64}:([0-9]{1,5})-[^:,]{0,64}:([0-9]{1,5})", val):
        hw["forwards"].append({"host_port": int(m.group(1)), "guest_port": int(m.group(2))})


def parse_qemu(argv):
    hw = empty()
    prog = os.path.basename(argv[0]) if argv else ""
    if prog.startswith("qemu-system-"):
        hw["other"].append(f"QEMU {prog[len('qemu-system-'):]}")
    netdevs, nics, smp = {}, [], None
    i = 1
    while i < len(argv):
        opt = argv[i].lstrip("-") if argv[i].startswith("-") else None
        val = argv[i + 1] if i + 1 < len(argv) else ""
        if opt is None:
            i += 1
            continue
        if opt in ("M", "machine"):
            hw["machine"] = _kv_opts(val)[0] or _kv_opts(val)[1].get("type")
        elif opt == "m":
            first, kv = _kv_opts(val)
            n = size_to_mb(first or kv.get("size", ""))
            hw["memory"] = mb(n) if n else None
        elif opt == "cpu":
            hw["cpu"] = val.split(",")[0]
        elif opt == "smp":
            first, kv = _kv_opts(val)
            count = first or kv.get("cpus")
            smp = count if count and count.isdigit() and len(count) < 5 else None
        elif opt in ("hda", "hdb", "hdc", "hdd", "cdrom"):
            hw["disks"].append({"unit": opt, "model": None, "file": val,
                                "kind": "cdrom" if opt == "cdrom" else "disk",
                                "readonly": opt == "cdrom"})
        elif opt == "drive":
            _, kv = _kv_opts(val)
            cd = kv.get("media") == "cdrom"
            hw["disks"].append({"unit": kv.get("if", "drive") + kv.get("index", ""),
                                "model": None, "file": kv.get("file"),
                                "kind": "cdrom" if cd else "disk",
                                "readonly": cd or kv.get("readonly") in ("on", "true")})
        elif opt in ("net", "nic"):
            _forwards(hw, val)
            first, kv = _kv_opts(val)
            if opt == "net" and first == "nic":
                nics.append({"unit": f"nic{len(nics)}", "model": kv.get("model"),
                             "mac": kv.get("macaddr"), "host_if": None})
            elif opt == "net" and first == "tap":
                if nics and nics[-1]["host_if"] is None:
                    nics[-1]["host_if"] = kv.get("ifname")
                else:
                    nics.append({"unit": f"nic{len(nics)}", "model": None, "mac": None,
                                 "host_if": kv.get("ifname")})
            elif opt == "nic" and first != "none":
                nics.append({"unit": f"nic{len(nics)}", "model": kv.get("model"),
                             "mac": kv.get("mac"),
                             "host_if": kv.get("ifname") if first == "tap" else None})
        elif opt == "netdev":
            _forwards(hw, val)
            first, kv = _kv_opts(val)
            if kv.get("id"):
                netdevs[kv["id"]] = kv.get("ifname") if first == "tap" else None
        elif opt == "device":
            first, kv = _kv_opts(val)
            if kv.get("netdev") is not None:
                nics.append({"unit": kv.get("id") or f"nic{len(nics)}", "model": first,
                             "mac": kv.get("mac"), "host_if": ("netdev", kv["netdev"])})
        else:
            i += 1 if (i + 1 >= len(argv) or argv[i + 1].startswith("-")) else 2
            continue
        i += 2
    if smp:
        hw["cpu"] = f"{smp} × {hw['cpu'] or 'CPU'}"
    for n in nics:
        if isinstance(n["host_if"], tuple):
            n["host_if"] = netdevs.get(n["host_if"][1])
        if n["mac"]:
            n["mac"] = n["mac"].lower()
    hw["nics"] = nics
    return hw


# --- gxemul --------------------------------------------------------------------

def _dict(x):
    return x if isinstance(x, dict) else {}


def _str(x):
    return x if isinstance(x, str) and x else None


def parse_gxemul(text):
    hw = empty()
    try:
        d = json.loads(text)
    except (ValueError, RecursionError):
        hw["problems"].append("not valid JSON")
        return hw
    d = _dict(d)
    m = _dict(d.get("machine"))
    model = _str(m.get("model")) or ""
    hw["machine"] = GXEMUL_MODELS.get(model) or " ".join(
        x for x in (_str(m.get("type")), model) if x) or None
    cpus = m.get("cpus") if isinstance(m.get("cpus"), int) else 0
    if _str(m.get("cpu")):
        hw["cpu"] = (f"{cpus} × " if cpus > 1 else "") + m["cpu"]
    mem = m.get("memory_mb")
    if isinstance(mem, int) and 0 < mem < 10 ** 9:
        hw["memory"] = mb(mem)
    drives = d.get("drives") if isinstance(d.get("drives"), list) else []
    for i, drv in enumerate(drives):
        if isinstance(drv, dict):
            unit = drv.get("id", i) if isinstance(drv.get("id", i), (int, str)) else i
            hw["disks"].append({"unit": f"id {unit}", "model": _str(drv.get("product")),
                                "file": _str(drv.get("file")),
                                "kind": "cdrom" if drv.get("kind") == "cdrom" else "disk",
                                "readonly": drv.get("readonly") is True})
    net = _dict(d.get("network"))
    if net.get("enabled") is True:
        mac = _str(net.get("mac"))
        hw["nics"].append({"unit": "net0", "model": None, "mac": mac.lower() if mac else None,
                           "host_if": _str(net.get("tapdev")) if net.get("type") == "bridged"
                           else None})
        if _str(net.get("type")) and net["type"] != "bridged":
            hw["other"].append(f"network: gxemul {net['type']}")
    if _dict(d.get("display")).get("enabled") is True:
        hw["other"].append("graphics display")
    for slot, rom in sorted(_dict(d.get("slots")).items()):
        if isinstance(rom, str):
            hw["other"].append(f"TURBOchannel slot {slot}: {os.path.basename(rom)}")
    return hw


# --- 86Box ----------------------------------------------------------------------

# 86Box's internal names for the machines and CPUs seen so far; others show as written.
B86_MACHINES = {"486sp3g": "ASUS PCI/I-486SP3G", "cubx": "ASUS CUBX",
                "p54tp4xe": "ASUS P/I-P54TP4XE"}
B86_CPUS = {"i486sx": "Intel 486SX", "i486dx": "Intel 486DX", "i486dx2": "Intel 486DX2",
            "pentium_p54c": "Pentium (P54C)", "pentium_mmx": "Pentium MMX",
            "celeron_mendocino": "Celeron (Mendocino)", "pentium2": "Pentium II"}


def _ini(text):
    """{section: {key: value}} from an INI file, last value winning."""
    out, out_sec = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            out_sec = out.setdefault(line[1:-1].strip(), {})
            continue
        if out_sec is not None and "=" in line:
            k, v = line.split("=", 1)
            out_sec[k.strip()] = v.strip()
    return out


def parse_86box(text):
    """86Box's 86box.cfg: machine, CPU and speed, memory, hard disks, CD-ROM
    images, network cards and the video card."""
    hw = empty()
    ini = _ini(text)
    m = ini.get("Machine", {})
    if m.get("machine"):
        hw["machine"] = B86_MACHINES.get(m["machine"], m["machine"])
    if m.get("cpu_family"):
        cpu = B86_CPUS.get(m["cpu_family"], m["cpu_family"])
        speed = m.get("cpu_speed", "")
        if re.fullmatch(r"[0-9]{1,12}", speed) and 0 < int(speed) < 10 ** 11:
            cpu += f", {round(int(speed) / 1e6)} MHz"
        hw["cpu"] = cpu
    if re.fullmatch(r"[0-9]{1,10}", m.get("mem_size", "")):
        hw["memory"] = mb(int(m["mem_size"]) // 1024)
    disks = ini.get("Hard disks", {})
    for key in sorted(disks):
        mt = re.fullmatch(r"hdd_([0-9]{1,3})_fn", key)
        if not mt:
            continue
        n = mt.group(1)
        params = [x.strip() for x in disks.get(f"hdd_{n}_parameters", "").split(",")]
        model = None
        if len(params) >= 5 and all(re.fullmatch(r"[0-9]{1,6}", x) for x in params[:3]):
            size = round(int(params[0]) * int(params[1]) * int(params[2]) * 512 / (1024 * 1024))
            model = f"{params[4].upper()} disk, {mb(size)}"
        hw["disks"].append({"unit": f"hdd {int(n)}", "model": model, "file": disks[key],
                            "kind": "disk", "readonly": False})
    drives = ini.get("Floppy and CD-ROM drives", {})
    for key in sorted(drives):
        mt = re.fullmatch(r"cdrom_([0-9]{1,3})_image_path", key)
        if mt and drives[key]:
            hw["disks"].append({"unit": f"cdrom {int(mt.group(1))}", "model": None,
                                "file": drives[key], "kind": "cdrom", "readonly": True})
    net = ini.get("Network", {})
    for key in sorted(net):
        mt = re.fullmatch(r"net_([0-9]{1,3})_card", key)
        if not mt or net[key] in ("", "none"):
            continue
        n = mt.group(1)
        kind = net.get(f"net_{n}_net_type", "")
        host = net.get(f"net_{n}_host_device") or None
        hw["nics"].append({"unit": f"net {int(n)}", "model": net[key], "mac": None,
                           "host_if": host if kind in ("pcap", "tap", "vde") else None})
        if kind == "slirp":
            hw["other"].append(f"net {int(n)}: SLiRP, user-mode NAT on the host")
    # SLiRP port forwards: [SLiRP Port Forwarding #1] 0_external = 2324, 0_internal = 23
    for sec, kv in ini.items():
        if not sec.startswith("SLiRP Port Forwarding"):
            continue
        for key in sorted(kv):
            mt = re.fullmatch(r"([0-9]{1,2})_external", key)
            inside = kv.get(f"{mt.group(1)}_internal", "") if mt else ""
            if mt and re.fullmatch(r"[0-9]{1,5}", kv[key]) and re.fullmatch(r"[0-9]{1,5}", inside):
                hw["forwards"].append({"host_port": int(kv[key]), "guest_port": int(inside)})
    video = ini.get("Video", {}).get("gfxcard")
    if video:
        hw["other"].append(f"video: {video}")
    return hw


# --- putting it together -------------------------------------------------------

PARSERS = {"simh": parse_simh, "es40": parse_es40, "gxemul": parse_gxemul,
           "86box": parse_86box}
MAX_BYTES = 256 * 1024          # far larger than any real emulator config
MAX_FILES = 8


def read_config(path):
    """Read an emulator config the guest conf names, defensively.

    Regular files only: O_NONBLOCK means a FIFO cannot hang us in open(), and
    the type check refuses it (and devices) before any read. Bounded size.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(0, "not a regular file")
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise OSError(0, f"larger than {MAX_BYTES // 1024} KB")
    return data.decode("utf-8", errors="replace")
CONFIG_SUFFIX = {"simh": ".ini", "es40": ".cfg", "gxemul": ".json"}


def config_files(cfg):
    """The files to read: `config` from the conf, else a guess from `command`."""
    if cfg.config_files:
        return cfg.config_files
    if cfg.kind == "86box":         # 86Box -P <dir> reads <dir>/86box.cfg
        args = cfg.command[1:]
        d = args[args.index("-P") + 1] if "-P" in args[:-1] else "."
        d = d if os.path.isabs(d) else os.path.join(cfg.workdir, d)
        return [os.path.join(d, "86box.cfg")]
    suffix = CONFIG_SUFFIX.get(cfg.kind)
    if not suffix:
        return []
    for arg in cfg.command[1:]:
        if arg.endswith(suffix):
            return [arg if os.path.isabs(arg) else os.path.join(cfg.workdir, arg)]
    return []


def apply_overrides(hw, overrides):
    for label, text in overrides.items():
        if label in ("machine", "cpu", "memory"):
            hw[label] = text
        else:
            hw["other"].append(f"{label}: {text}")
    return hw


def describe(cfg, read=None):
    """The guest's hardware. `read(path)` returns file text (for tests)."""
    read = read or read_config
    hw = empty()
    try:
        if cfg.kind == "qemu":
            hw = parse_qemu(cfg.command)
            hw["sources"] = ["command"]
        elif cfg.kind in PARSERS:
            for path in config_files(cfg)[:MAX_FILES]:
                try:
                    text = read(path)
                except OSError as exc:
                    hw["problems"].append(f"cannot read {path}: {exc.strerror or exc}")
                    continue
                part = (parse_simh(text, cfg.command[0]) if cfg.kind == "simh"
                        else PARSERS[cfg.kind](text))
                for key in ("machine", "cpu", "memory"):
                    hw[key] = hw[key] or part[key]
                for key in ("disks", "nics", "other", "problems", "forwards"):
                    hw[key].extend(part[key])
                hw["sources"].append(path)
    except Exception as exc:                # a parser bug must not break `info`
        hw = empty()
        hw["problems"].append(f"could not read the {cfg.kind} configuration: {exc}")
    return apply_overrides(hw, cfg.hw)


def format_hardware(hw):
    out = []

    def line(key, value):
        out.append(f"{key:<12}{value}")
    for key in ("machine", "cpu", "memory"):
        if hw[key]:
            line(key, hw[key])
    for d in hw["disks"]:
        bits = [d["model"] or "", d["file"] or "", d["kind"] if d["kind"] != "disk" else "",
                "read-only" if d["readonly"] else ""]
        line("disk", f"{d['unit']}: " + ", ".join(b for b in bits if b))
    for n in hw["nics"]:
        bits = [n["model"] or "", n["mac"] or "", f"on {n['host_if']}" if n["host_if"] else ""]
        line("nic", f"{n['unit']}: " + ", ".join(b for b in bits if b))
    for o in hw["other"]:
        line("other", o)
    for f in hw.get("forwards", []):
        line("forward", f"host port {f['host_port']} -> guest port {f['guest_port']}")
    line("read from", ", ".join(hw["sources"]) or "nothing (set `config` in the conf)")
    for p in hw["problems"]:
        line("problem", p)
    return "\n".join(out)
