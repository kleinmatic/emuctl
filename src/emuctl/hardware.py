# SPDX-License-Identifier: GPL-2.0-only
"""What machine a guest is: read from the emulator's own configuration.

One parser per emulator kind. Each takes the config text and returns the same
shape, leaving out anything it does not understand. None of them raises: an
emulator config is the user's file, written for the emulator, not for us.

    {"machine": str|None, "cpu": str|None, "memory": str|None,
     "disks": [{"unit", "model", "file", "kind", "readonly"}],
     "nics":  [{"unit", "model", "mac", "host_if"}],
     "other": [str], "sources": [path], "problems": [str]}

`hw.<label>` lines in the guest conf take precedence over anything parsed.
"""
import json
import os
import re
import shlex

TAPE_PREFIXES = ("tq", "ts", "tu", "tm", "mt", "ta")
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
            "other": [], "sources": [], "problems": []}


def mb(n):
    """'128 MB', or '2 GB' for a whole number of gigabytes."""
    n = int(n)
    return f"{n // 1024} GB" if n >= 1024 and n % 1024 == 0 else f"{n} MB"


def size_to_mb(text):
    """'64m' -> 64, '1G' -> 1024, '192' -> 192 (QEMU's default unit is MB)."""
    m = re.fullmatch(r"(\d+)\s*([kKmMgG]?)[bB]?", text.strip())
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
            if dev.startswith(("xq", "xu", "ni", "ec")) or any(
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
                if first in ("enable", "disable", "online", "offline") or "=" in first:
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
            if target.lower().startswith(("tap:", "eth:", "vde:", "nat:", "udp:", "pcap:")):
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

def _props(body):
    return {k: v.strip().strip('"') for k, v in re.findall(r"([\w.]+)\s*=\s*([^;]+);", body)}


def parse_es40(text):
    hw = empty()
    text = re.sub(r"//[^\n]*|#[^\n]*", "", text)
    if re.search(r"=\s*tsunami\b", text):
        hw["machine"] = "AlphaServer ES40"
    m = re.search(r"memory\.bits\s*=\s*(\d+)", text)
    if m and 20 <= int(m.group(1)) <= 40:
        hw["memory"] = mb(2 ** int(m.group(1)) // (1024 * 1024))
    cpus = re.findall(r"\bcpu\d+\s*=\s*(\w+)\s*(?:\{([^}]*)\})?", text)
    if cpus:
        model = cpus[0][0].upper()
        speed = _props(cpus[0][1] or "").get("speed", "")
        sm = re.fullmatch(r"(\d+)\s*([MG])", speed.upper())
        mhz = (int(sm.group(1)) * (1000 if sm.group(2) == "G" else 1)) if sm else None
        hw["cpu"] = f"{len(cpus)} × {model}" + (f", {mhz} MHz" if mhz else "")
    for unit, dtype, body in re.findall(r"\b(disk\d+\.\d+)\s*=\s*(\w+)\s*\{([^}]*)\}", text):
        p = _props(body)
        cdrom = p.get("cdrom", "").lower() == "true"
        hw["disks"].append({"unit": unit, "model": dtype, "file": p.get("file"),
                            "kind": "cdrom" if cdrom else "disk",
                            "readonly": cdrom or p.get("read_only", "").lower() == "true"})
    for unit, model, body in re.findall(r"\b(pci\d+\.\d+)\s*=\s*(dec21143|e1000)\s*\{([^}]*)\}",
                                        text):
        p = _props(body)
        adapter = p.get("adapter")
        hw["nics"].append({"unit": unit,
                           "model": "DEC 21143" if model == "dec21143" else model.upper(),
                           "mac": p["mac"].lower().replace("-", ":") if p.get("mac") else None,
                           "host_if": os.path.basename(adapter) if adapter else None})
    for unit, body in re.findall(r"\b(serial\d+)\s*=\s*serial\s*\{([^}]*)\}", text):
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


def parse_qemu(argv):
    hw = empty()
    prog = os.path.basename(argv[0]) if argv else ""
    if prog.startswith("qemu-system-"):
        hw["other"].append(f"QEMU {prog[len('qemu-system-'):]}")
    netdevs, nics = {}, []
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
            if count and count.isdigit():
                hw["cpu"] = f"{count} × {hw['cpu'] or 'CPU'}"
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
            elif opt == "nic":
                nics.append({"unit": f"nic{len(nics)}", "model": kv.get("model"),
                             "mac": kv.get("mac"),
                             "host_if": kv.get("ifname") if first == "tap" else None})
        elif opt == "netdev":
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
    for n in nics:
        if isinstance(n["host_if"], tuple):
            n["host_if"] = netdevs.get(n["host_if"][1])
        if n["mac"]:
            n["mac"] = n["mac"].lower()
    hw["nics"] = nics
    return hw


# --- gxemul --------------------------------------------------------------------

def parse_gxemul(text):
    hw = empty()
    try:
        d = json.loads(text)
    except ValueError:
        hw["problems"].append("not valid JSON")
        return hw
    if not isinstance(d, dict):
        return hw
    m = d.get("machine") if isinstance(d.get("machine"), dict) else {}
    model = m.get("model") or ""
    hw["machine"] = GXEMUL_MODELS.get(model) or " ".join(
        x for x in (m.get("type"), model) if x) or None
    if m.get("cpu"):
        hw["cpu"] = (f"{m['cpus']} × " if m.get("cpus", 0) > 1 else "") + str(m["cpu"])
    if m.get("memory_mb"):
        hw["memory"] = mb(m["memory_mb"])
    for i, drv in enumerate(d.get("drives") or []):
        if isinstance(drv, dict):
            hw["disks"].append({"unit": f"id {drv.get('id', i)}",
                                "model": drv.get("product") or None, "file": drv.get("file"),
                                "kind": "cdrom" if drv.get("kind") == "cdrom" else "disk",
                                "readonly": bool(drv.get("readonly"))})
    net = d.get("network") if isinstance(d.get("network"), dict) else {}
    if net.get("enabled"):
        hw["nics"].append({"unit": "net0", "model": None, "mac": (net.get("mac") or "").lower()
                           or None,
                           "host_if": net.get("tapdev") if net.get("type") == "bridged" else None})
        if net.get("type") and net.get("type") != "bridged":
            hw["other"].append(f"network: gxemul {net['type']}")
    disp = d.get("display") if isinstance(d.get("display"), dict) else {}
    if disp.get("enabled"):
        hw["other"].append("graphics display")
    for slot, rom in sorted((d.get("slots") or {}).items()):
        hw["other"].append(f"TURBOchannel slot {slot}: {os.path.basename(str(rom))}")
    return hw


# --- putting it together -------------------------------------------------------

PARSERS = {"simh": parse_simh, "es40": parse_es40, "gxemul": parse_gxemul}
CONFIG_SUFFIX = {"simh": ".ini", "es40": ".cfg", "gxemul": ".json"}


def config_files(cfg):
    """The files to read: `config` from the conf, else a guess from `command`."""
    if cfg.config_files:
        return cfg.config_files
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
    def default_read(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(1_000_000)
    read = read or default_read
    hw = empty()
    try:
        if cfg.kind == "qemu":
            hw = parse_qemu(cfg.command)
            hw["sources"] = ["command"]
        elif cfg.kind in PARSERS:
            for path in config_files(cfg):
                try:
                    text = read(path)
                except OSError as exc:
                    hw["problems"].append(f"cannot read {path}: {exc.strerror or exc}")
                    continue
                part = (parse_simh(text, cfg.command[0]) if cfg.kind == "simh"
                        else PARSERS[cfg.kind](text))
                for key in ("machine", "cpu", "memory"):
                    hw[key] = hw[key] or part[key]
                for key in ("disks", "nics", "other", "problems"):
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
    line("read from", ", ".join(hw["sources"]) or "nothing (set `config` in the conf)")
    for p in hw["problems"]:
        line("problem", p)
    return "\n".join(out)
