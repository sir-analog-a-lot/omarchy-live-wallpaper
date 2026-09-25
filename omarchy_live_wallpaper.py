#!/usr/bin/env python3
"""
Omarchy Live Wallpaper - vintage brass gauges over a cubist background.

Shows, in real time: disk temperature, CPU temperature, CPU package power, RAM load and disk busy %.
All sensors are auto-detected (SATA/NVMe, Intel/AMD); anything missing is shown as "--" / "N/A".

Modes:
  omarchy_live_wallpaper.py                  run as a wlr-layer-shell wallpaper (GTK4 + gtk4-layer-shell)
  omarchy_live_wallpaper.py --print-sensors  print detected sensors + current readings and exit (no GTK)
  omarchy_live_wallpaper.py --render-png F   render a single frame to PNG (headless, cairo only)

Configuration (optional): ~/.config/omarchy-live-wallpaper/config.toml  (see config.example.toml)
"""
import argparse
import glob
import math
import os
import random
import re
import sys
import time

try:
    import cairo            # python-cairo; only needed for drawing (not for --print-sensors)
except ImportError:         # pragma: no cover
    cairo = None

APP_NAME = "omarchy-live-wallpaper"
APP_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_BG = os.path.join(APP_DIR, "background.png")
SERIF = "C059"            # URW Century Schoolbook (Arch package: gsfonts)
MONO = "Nimbus Mono PS"   # URW Courier clone -> typewriter look (gsfonts)

# For testing the detection logic against a fake /sys + /proc tree: OLW_SYSROOT=/path/to/tree
SYSROOT = os.environ.get("OLW_SYSROOT", "").rstrip("/")

POWER_TIERS = (15, 25, 35, 45, 65, 95, 125, 170, 250, 350, 500)


def P(path):
    """Map an absolute /sys, /proc or /dev path into SYSROOT (identity on a real system)."""
    return SYSROOT + path if SYSROOT else path


def U(path):
    """Inverse of P(): strip SYSROOT from a resolved path."""
    if SYSROOT and path.startswith(SYSROOT + "/"):
        return path[len(SYSROOT):]
    return path


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return default


def _read_int(path, default=None):
    v = _read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _real(path):
    return U(os.path.realpath(P(path))) if os.path.lexists(P(path)) else ""


# --------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------
DEFAULTS = {
    "disk": "auto",              # "auto" or a block device name: "nvme0n1", "sda", "/dev/sdb"
    "power_max": 0,              # full-scale watts for the CPU POWER gauge; 0 = auto
    "interval": 1.0,             # seconds between sensor reads (min 0.25)
    "monitors": "all",           # "all", "primary", "DP-1" or ["DP-1", "HDMI-A-1"]
    "background": "default",     # "default" (bundled painting), "none" (transparent), or a path
    "layer": "bottom",           # layer-shell layer: background | bottom | top | overlay
    "cpu_gauge": "auto",         # auto (power, falls back to load) | power | load
    "drive_temp_sensor": "",     # optional explicit path, e.g. /sys/class/hwmon/hwmon3/temp1_input
    "cpu_temp_sensor": "",       # optional explicit path
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME, "config.toml")


def load_config(path=None, warn=True):
    """Return (config dict, path used or None). Missing file -> defaults. Bad values -> defaults + warning."""
    cfg = dict(DEFAULTS)
    path = path or config_path()
    if not os.path.exists(path):
        return cfg, None
    try:
        if path.endswith(".json"):
            import json
            with open(path) as f:
                user = json.load(f)
        else:
            import tomllib
            with open(path, "rb") as f:
                user = tomllib.load(f)
    except Exception as e:  # noqa: BLE001 - never crash the wallpaper over a typo
        if warn:
            print(f"{APP_NAME}: ignoring config {path}: {e}", file=sys.stderr)
        return cfg, None
    for k, v in user.items():
        if k not in DEFAULTS:
            if warn:
                print(f"{APP_NAME}: unknown config key '{k}' (ignored)", file=sys.stderr)
            continue
        cfg[k] = v
    # validation / normalisation
    try:
        cfg["interval"] = max(0.25, float(cfg["interval"]))
    except (TypeError, ValueError):
        cfg["interval"] = DEFAULTS["interval"]
    try:
        cfg["power_max"] = max(0.0, float(cfg["power_max"]))
    except (TypeError, ValueError):
        cfg["power_max"] = 0.0
    if cfg["layer"] not in ("background", "bottom", "top", "overlay"):
        cfg["layer"] = DEFAULTS["layer"]
    if cfg["cpu_gauge"] not in ("auto", "power", "load"):
        cfg["cpu_gauge"] = DEFAULTS["cpu_gauge"]
    if not isinstance(cfg["monitors"], (str, list)):
        cfg["monitors"] = "all"
    return cfg, path


def resolve_background(value):
    """'default' -> bundled PNG, 'none'/'' -> None (transparent), else expanded path."""
    if value in (None, "", "none", False):
        return None
    if value == "default":
        return DEFAULT_BG
    return os.path.expandvars(os.path.expanduser(str(value)))


# --------------------------------------------------------------------------------------------
# Sensor detection (pure /proc + /sys, no third-party deps; testable with --print-sensors)
# --------------------------------------------------------------------------------------------
SKIP_DISK = re.compile(r"^(loop|ram|zram|dm-|md|sr|fd|nbd|mtdblock|zd)")


def _is_whole_disk(name):
    return bool(name) and os.path.isdir(P(f"/sys/block/{name}")) and not SKIP_DISK.match(name)


def _to_whole_disk(name, depth=0):
    """Walk dm-crypt/LVM slaves and partitions down to a whole disk (e.g. dm-0 -> sda2 -> sda)."""
    if depth > 8 or not name:
        return name
    slaves = sorted(glob.glob(P(f"/sys/class/block/{name}/slaves/*")))
    if slaves:
        return _to_whole_disk(os.path.basename(slaves[0]), depth + 1)
    if os.path.exists(P(f"/sys/class/block/{name}/partition")):
        return os.path.basename(os.path.dirname(os.path.realpath(P(f"/sys/class/block/{name}"))))
    return name


def root_disk():
    """Whole-disk kernel name (e.g. 'sda', 'nvme0n1') backing '/', or None."""
    src = None
    try:
        with open(P("/proc/self/mountinfo")) as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4 and parts[4] == "/":
                    src = parts[parts.index("-") + 2]   # last '/' mount wins (over-mounts)
    except (OSError, ValueError, IndexError):
        pass
    if not src or not src.startswith("/dev/"):
        return None
    name = os.path.basename(os.path.realpath(P(src)))
    disk = _to_whole_disk(name)
    return disk if _is_whole_disk(disk) else None


def disk_io_table():
    """{disk name: io_ticks_ms} for whole, real disks in /proc/diskstats."""
    out = {}
    try:
        with open(P("/proc/diskstats")) as f:
            for line in f:
                p = line.split()
                if len(p) > 12 and _is_whole_disk(p[2]):
                    out[p[2]] = int(p[12])
    except (OSError, ValueError):
        pass
    return out


def pick_disk(override="auto"):
    """(disk, how) - config override, else the disk backing '/', else the busiest real disk."""
    if override and override != "auto":
        name = os.path.basename(str(override))
        return name, "config"
    d = root_disk()
    if d:
        return d, "root filesystem"
    table = disk_io_table()
    if table:
        return max(table, key=table.get), "busiest disk"
    return None, "none found"


def _hwmons():
    for h in sorted(glob.glob(P("/sys/class/hwmon/hwmon*"))):
        yield h, _read(h + "/name", "") or "", U(os.path.realpath(h + "/device")) if os.path.exists(h + "/device") else ""


def find_drive_temp(disk, strict=False):
    """(path, label) for the temperature of `disk`. Unless strict (disk set in the config), falls back
    to another drive's sensor, labelled with that drive's name."""
    cands = []
    disk = disk or ""
    disk_dev = _real(f"/sys/block/{disk}/device") if disk else ""
    nvme_ctrl = re.match(r"^(nvme\d+)", disk)
    nvme_ctrl = nvme_ctrl.group(1) if nvme_ctrl else None
    for h, name, dev in _hwmons():
        if name == "drivetemp":
            mine = bool(disk_dev) and dev == disk_dev
            owner = disk if mine else _block_for_scsi(dev) or os.path.basename(dev)
            cands.append((0 if mine else 2, h + "/temp1_input", f"DRIVETEMP {owner}"))
        elif name == "nvme":
            mine = bool(disk.startswith("nvme") and dev and (
                dev == disk_dev or disk_dev.startswith(dev + "/") or os.path.basename(dev) == nvme_ctrl))
            inp = h + "/temp1_input"
            for lab in sorted(glob.glob(h + "/temp*_label")):
                if _read(lab, "") == "Composite":
                    inp = lab[:-len("_label")] + "_input"
                    break
            owner = disk if mine else (os.path.basename(dev) if dev else "nvme")
            cands.append((0 if mine else 1, inp, f"{owner} COMPOSITE" if mine else f"{owner} (NOT ROOT)"))
    cands.sort()
    for rank, p, s in cands:
        if strict and rank != 0:
            continue
        if _read(p) is not None:
            return p, s.upper()
    return None, None


def _block_for_scsi(dev):
    try:
        blocks = os.listdir(P(dev) + "/block")
        return blocks[0] if blocks else None
    except OSError:
        return None


def find_cpu_temp():
    """(path, label): coretemp package (Intel) / k10temp|zenpower Tdie/Tctl (AMD) / thermal zones."""
    pref = []
    for h, name, _dev in _hwmons():
        labels = {}
        for lab in glob.glob(h + "/temp*_label"):
            labels[_read(lab, "")] = lab[:-len("_label")] + "_input"
        if name == "coretemp":
            for l, inp in labels.items():
                if l.startswith("Package"):
                    pref.append((0, inp, "CORETEMP PACKAGE"))
            pref.append((3, h + "/temp1_input", "CORETEMP"))
        elif name in ("k10temp", "zenpower"):
            for l, rank in (("Tdie", 0), ("Tctl", 1)):
                if l in labels:
                    pref.append((rank, labels[l], f"{name} {l}"))
            pref.append((3, h + "/temp1_input", name))
        elif name in ("cpu_thermal", "soc_thermal"):
            pref.append((2, h + "/temp1_input", name))
        elif name == "acpitz":
            pref.append((5, h + "/temp1_input", "ACPI THERMAL ZONE"))
    for z in sorted(glob.glob(P("/sys/class/thermal/thermal_zone*"))):
        t = _read(z + "/type", "") or ""
        if t == "x86_pkg_temp":
            pref.append((2, z + "/temp", "THERMAL X86_PKG_TEMP"))
        elif t:
            pref.append((6, z + "/temp", "THERMAL " + t))
    pref.sort()
    for _, p, s in pref:
        v = _read_int(p)
        if v is not None and v > 0:
            return p, s.upper()
    return None, None


class PowerSource:
    """CPU package energy counters: powercap RAPL (Intel and AMD both use 'intel-rapl:N'),
    falling back to the amd_energy hwmon driver. Sums all packages (multi-socket)."""

    def __init__(self):
        self.kind, self.zones, self.label = None, [], "NO RAPL"
        for d in sorted(glob.glob(P("/sys/class/powercap/*:*"))):
            base = os.path.basename(d)
            if not re.match(r"^intel-rapl:\d+$", base):
                continue                                    # skip sub-zones (core/uncore) and mmio duplicates
            if not (_read(d + "/name", "") or "").startswith("package"):
                continue
            wrap = _read_int(d + "/max_energy_range_uj", 0) or 0
            self.zones.append((d + "/energy_uj", wrap, d))
        if self.zones:
            self.kind, self.label = "rapl", "RAPL PKG"
        else:
            for h, name, _dev in _hwmons():
                if name == "amd_energy":
                    for lab in sorted(glob.glob(h + "/energy*_label")):
                        if (_read(lab, "") or "").startswith("Esocket"):
                            self.zones.append((lab[:-len("_label")] + "_input", 0, h))
            if self.zones:
                self.kind, self.label = "amd_energy", "AMD_ENERGY"

    @property
    def present(self):
        return bool(self.zones)

    def read(self):
        """Total energy in microjoules, or None if missing/unreadable (e.g. root-only energy_uj)."""
        if not self.zones:
            return None
        total = 0
        for path, _wrap, _d in self.zones:
            v = _read_int(path)
            if v is None:
                return None
            total += v
        return total

    def wrap(self):
        return sum(w for _p, w, _d in self.zones)

    def limit_watts(self):
        """Sum of package power constraints (max power, else PL1) in watts; 0 if unknown."""
        tot = 0.0
        for _p, _w, d in self.zones:
            if self.kind != "rapl":
                continue
            v = _read_int(d + "/constraint_0_max_power_uw", 0) or 0
            if v <= 0:
                v = _read_int(d + "/constraint_0_power_limit_uw", 0) or 0
            tot += v / 1e6
        return tot


def power_scale(src, override=0.0):
    """(full-scale watts, how). Override wins; else next tier above the RAPL limit; else a guess by core count."""
    if override and override > 0:
        return float(override), "config"
    ref = src.limit_watts() if src else 0.0
    if ref > 0:
        for t in POWER_TIERS:
            if t >= ref - 0.01:
                return float(t), f"RAPL limit {ref:g} W"
        return float(POWER_TIERS[-1]), f"RAPL limit {ref:g} W"
    n = os.cpu_count() or 4
    guess = 15 if n <= 4 else 35 if n <= 8 else 65 if n <= 16 else 125
    return float(guess), f"guess from {n} CPUs"


class Metrics:
    def __init__(self, cfg=None):
        cfg = cfg or dict(DEFAULTS)
        self.cfg = cfg
        self.disk, self.disk_how = pick_disk(cfg.get("disk", "auto"))
        self.t_prev = time.monotonic()
        self.power = PowerSource()
        self.power_scale, self.power_scale_how = power_scale(self.power, cfg.get("power_max", 0))
        self.cpu_mode = cfg.get("cpu_gauge", "auto")
        self.e_prev = self.power.read()
        self.power_ok = self.e_prev is not None
        self.stat_prev = self._cpustat()
        self.io_prev = self._io_ticks()
        self.last_discover = 0
        self._discover()

    @property
    def show_power(self):
        """Whether the CPU gauge is the POWER variant (vs LOAD %)."""
        if self.cpu_mode == "power":
            return True
        if self.cpu_mode == "load":
            return False
        return self.power_ok

    # -- temperature sensors (re-run periodically so e.g. drivetemp can appear after modprobe)
    def _discover(self):
        self.last_discover = time.monotonic()
        o = self.cfg.get("drive_temp_sensor") or ""
        if o:
            self.drive_temp_path, self.drive_temp_src = o, "CONFIG SENSOR"
        else:
            self.drive_temp_path, self.drive_temp_src = find_drive_temp(self.disk, strict=self.disk_how == "config")
        o = self.cfg.get("cpu_temp_sensor") or ""
        if o:
            self.int_temp_path, self.int_temp_src = o, "CONFIG SENSOR"
        else:
            self.int_temp_path, self.int_temp_src = find_cpu_temp()

    @staticmethod
    def _cpustat():
        try:
            with open(P("/proc/stat")) as f:
                v = [int(x) for x in f.readline().split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            return sum(v[:8]), idle
        except (OSError, ValueError, IndexError):
            return None

    def _io_ticks(self):
        if not self.disk:
            return None
        try:
            with open(P("/proc/diskstats")) as f:
                for line in f:
                    p = line.split()
                    if len(p) > 12 and p[2] == self.disk:
                        return int(p[12])
        except (OSError, IndexError, ValueError):
            pass
        return None

    def _drive_hint(self):
        if self.disk and self.disk.startswith("sd") and not os.path.isdir(P("/sys/module/drivetemp")):
            return "MODPROBE DRIVETEMP"
        return "NO SENSOR"

    def sample(self):
        now = time.monotonic()
        dt = max(now - self.t_prev, 1e-3)
        self.t_prev = now
        if now - self.last_discover > 30 and (self.drive_temp_path is None or self.int_temp_path is None):
            self._discover()
        out = {}
        # CPU utilisation (always computed; the LOAD variant and the POWER sub-readout use it)
        st = self._cpustat()
        util = None
        if st and self.stat_prev:
            dtot, didle = st[0] - self.stat_prev[0], st[1] - self.stat_prev[1]
            util = 100.0 * (1 - didle / dtot) if dtot > 0 else 0.0
        self.stat_prev = st
        e = self.power.read()
        watts = None
        if e is not None and self.e_prev is not None:
            de = e - self.e_prev
            if de < 0:
                de = de + self.power.wrap() if self.power.wrap() else None
            watts = de / 1e6 / dt if de is not None else None
        self.e_prev = e
        self.power_ok = e is not None
        if self.show_power:
            if e is None:
                out["cpu"] = (None, "power", "NO RAPL ACCESS" if self.power.present else "NO RAPL SENSOR")
            else:
                lbl = self.power.label
                out["cpu"] = (watts, "power", f"{lbl} · {util:.0f}% LOAD" if util is not None else lbl)
        else:
            out["cpu"] = (util, "util", "ALL CORES")
        # temperatures
        v = _read_int(self.drive_temp_path) if self.drive_temp_path else None
        out["disktemp"] = (v / 1000.0 if v is not None else None, "temp",
                           self.drive_temp_src or self._drive_hint())
        v = _read_int(self.int_temp_path) if self.int_temp_path else None
        out["cputemp"] = (v / 1000.0 if v is not None else None, "temp", self.int_temp_src or "NO SENSOR")
        # memory
        mem = {}
        try:
            with open(P("/proc/meminfo")) as f:
                for line in f:
                    k, rest = line.split(":", 1)
                    mem[k] = int(rest.split()[0])
        except (OSError, ValueError, IndexError):
            pass
        if mem.get("MemTotal"):
            used = mem["MemTotal"] - mem.get("MemAvailable", mem.get("MemFree", 0))
            out["ram"] = (100.0 * used / mem["MemTotal"], "pct",
                          f"{used / 1048576:.1f} / {mem['MemTotal'] / 1048576:.1f} GiB")
        else:
            out["ram"] = (None, "pct", "")
        # disk busy %
        io = self._io_ticks()
        busy = None
        if io is not None and self.io_prev is not None:
            busy = max(0.0, min(100.0, (io - self.io_prev) / (dt * 1000.0) * 100.0))
        self.io_prev = io
        try:
            s = os.statvfs("/")
            full = 100.0 * (1 - s.f_bavail / float(s.f_blocks)) if s.f_blocks else None
        except OSError:
            full = None
        name = (self.disk or "NO DISK").upper()
        out["diskload"] = (busy, "pct", f"{name} · {full:.0f}% FULL" if full is not None else name)
        return out


# --------------------------------------------------------------------------------------------
# Gauge painting (pure cairo)
# --------------------------------------------------------------------------------------------
START = math.radians(135.0)
SWEEP = math.radians(270.0)


class GaugeSpec:
    def __init__(self, key, label, unit, vmin, vmax, major, warn, fmt, idx, missing="--"):
        self.key, self.label, self.unit = key, label, unit
        self.vmin, self.vmax, self.major, self.warn, self.fmt, self.idx = vmin, vmax, major, warn, fmt, idx
        self.missing = missing   # readout text when the sensor is absent/unreadable
        self.R = 100.0

    @property
    def size(self):          # widget box edge (includes the dark legibility halo)
        return int(math.ceil(self.R * 2 * 1.42))

    def frac(self, v):
        return max(0.0, min(1.0, (v - self.vmin) / float(self.vmax - self.vmin)))


def make_specs(metrics):
    """The five gauges, left to right. The CPU gauge is POWER (watts) when RAPL is readable, else LOAD %."""
    cpu_power = metrics is None or metrics.show_power
    pmax = metrics.power_scale if metrics else 15.0
    specs = [
        GaugeSpec("disktemp", "DISK TEMP", "DEGREES CELSIUS", 0, 80, 20, 55, "{:.0f}°C", 0),
        GaugeSpec("cputemp", "CPU TEMP", "DEGREES CELSIUS", 0, 100, 20, 85, "{:.0f}°C", 1),
        (GaugeSpec("cpu", "CPU POWER", "WATTS", 0, pmax, pmax / 5.0, pmax * 0.8, "{:.1f} W", 2, missing="N/A")
         if cpu_power else
         GaugeSpec("cpu", "CPU LOAD", "PER CENT", 0, 100, 20, 85, "{:.0f} %", 2)),
        GaugeSpec("ram", "RAM LOAD", "PER CENT", 0, 100, 20, 85, "{:.0f} %", 3),
        GaugeSpec("diskload", "DISK LOAD", "PER CENT BUSY", 0, 100, 20, 90, "{:.0f} %", 4),
    ]
    return specs


def layout(specs, W, H, top_reserved=0):
    """Place the gauges on a gentle arch in the lower-middle of the screen. Returns [(spec, cx, cy)]."""
    usable_h = H - top_reserved
    R = min(usable_h * 0.155, W * 0.155 * 0.44)
    out = []
    for k, s in zip((-2, -1, 0, 1, 2), specs):
        s.R = R * (1.18 if k == 0 else 1.0)
        cx = W * (0.5 + k * 0.155)
        cy = top_reserved + usable_h * (0.60 + 0.045 * k * k)
        out.append((s, cx, cy))
    return out


def _font(cr, family, size, bold=False, italic=False):
    cr.select_font_face(family, cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)


def _ctext(cr, text, x, y, spacing=0.0):
    """Draw text centred at x with baseline-middle at y; optional letter spacing."""
    if spacing <= 0:
        e = cr.text_extents(text)
        cr.move_to(x - (e.x_advance) / 2.0, y - (e.y_bearing + e.height / 2.0))
        cr.show_text(text)
        return
    widths = [cr.text_extents(ch).x_advance for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    e = cr.text_extents(text)
    xx = x - total / 2.0
    yy = y - (e.y_bearing + e.height / 2.0)
    for ch, w in zip(text, widths):
        cr.move_to(xx, yy)
        cr.show_text(ch)
        xx += w + spacing


def _num(v):
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.1f}"


def make_face(spec, scale):
    """Static layer (halo, bezel, dial, ticks, numerals, labels, readout window) -> cached surface."""
    S = spec.size
    px = int(math.ceil(S * scale))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    surf.set_device_scale(scale, scale)
    cr = cairo.Context(surf)
    cr.translate(S / 2.0, S / 2.0)
    R = spec.R
    rnd = random.Random(1881 + spec.idx)

    # legibility halo (darkens the painting behind the instrument)
    g = cairo.RadialGradient(0, 0, R * 0.85, 0, 0, R * 1.42)
    g.add_color_stop_rgba(0, 0.05, 0.03, 0.02, 0.55)
    g.add_color_stop_rgba(0.45, 0.05, 0.03, 0.02, 0.28)
    g.add_color_stop_rgba(1, 0.05, 0.03, 0.02, 0.0)
    cr.arc(0, 0, R * 1.42, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    # drop shadow
    g = cairo.RadialGradient(R * 0.05, R * 0.08, R * 0.9, R * 0.05, R * 0.08, R * 1.12)
    g.add_color_stop_rgba(0, 0, 0, 0, 0.65); g.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.arc(R * 0.05, R * 0.08, R * 1.12, 0, 2 * math.pi); cr.set_source(g); cr.fill()

    # outer brass bezel
    g = cairo.LinearGradient(-R, -R, R, R)
    for off, c in ((0, "#f6e3a1"), (0.22, "#d2a857"), (0.48, "#8a6026"), (0.7, "#5a3c15"), (0.88, "#b8904a"), (1, "#e7c983")):
        g.add_color_stop_rgb(off, *[int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    cr.arc(0, 0, R, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    cr.arc(0, 0, R, 0, 2 * math.pi); cr.set_source_rgba(0.18, 0.11, 0.04, 0.9); cr.set_line_width(R * 0.012); cr.stroke()
    # beaded (knurled) ring
    n = 64
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = math.cos(a) * R * 0.945, math.sin(a) * R * 0.945
        gb = cairo.RadialGradient(x - R * 0.008, y - R * 0.008, 0, x, y, R * 0.022)
        lit = 0.5 + 0.5 * math.cos(a + math.pi * 0.75)   # light from top-left
        gb.add_color_stop_rgba(0, 1, 0.95, 0.75, 0.55 + 0.4 * lit)
        gb.add_color_stop_rgba(1, 0.35, 0.22, 0.07, 0.9)
        cr.arc(x, y, R * 0.02, 0, 2 * math.pi); cr.set_source(gb); cr.fill()
    # inner recessed step
    g = cairo.LinearGradient(-R, -R, R, R)
    g.add_color_stop_rgb(0, 0.35, 0.23, 0.08); g.add_color_stop_rgb(0.5, 0.72, 0.53, 0.24); g.add_color_stop_rgb(1, 0.96, 0.84, 0.52)
    cr.arc(0, 0, R * 0.905, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    cr.arc(0, 0, R * 0.868, 0, 2 * math.pi); cr.set_source_rgba(0.15, 0.09, 0.03, 1); cr.fill()

    # aged ivory dial
    D = R * 0.855
    g = cairo.RadialGradient(-R * 0.15, -R * 0.2, R * 0.05, 0, 0, D)
    g.add_color_stop_rgb(0, 0.965, 0.925, 0.815)
    g.add_color_stop_rgb(0.65, 0.925, 0.86, 0.70)
    g.add_color_stop_rgb(1, 0.78, 0.68, 0.49)
    cr.arc(0, 0, D, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    cr.save(); cr.arc(0, 0, D, 0, 2 * math.pi); cr.clip()
    # patina: soft stains + foxing specks
    for _ in range(28):
        x, y = rnd.uniform(-D, D), rnd.uniform(-D, D)
        r = rnd.uniform(0.04, 0.22) * R
        gs = cairo.RadialGradient(x, y, 0, x, y, r)
        gs.add_color_stop_rgba(0, 0.45, 0.32, 0.12, rnd.uniform(0.04, 0.10)); gs.add_color_stop_rgba(1, 0.45, 0.32, 0.12, 0)
        cr.arc(x, y, r, 0, 2 * math.pi); cr.set_source(gs); cr.fill()
    for _ in range(40):
        x, y = rnd.uniform(-D, D), rnd.uniform(-D, D)
        cr.arc(x, y, rnd.uniform(0.003, 0.009) * R, 0, 2 * math.pi)
        cr.set_source_rgba(0.40, 0.26, 0.10, rnd.uniform(0.15, 0.4)); cr.fill()
    # faint guilloche rings
    cr.set_line_width(R * 0.003)
    for i in range(10):
        cr.arc(0, 0, R * (0.12 + i * 0.028), 0, 2 * math.pi); cr.set_source_rgba(0.45, 0.33, 0.15, 0.10); cr.stroke()
    # inner edge shading (dial set into the case)
    g = cairo.RadialGradient(0, 0, D * 0.82, 0, 0, D)
    g.add_color_stop_rgba(0, 0, 0, 0, 0); g.add_color_stop_rgba(1, 0.2, 0.12, 0.03, 0.35)
    cr.arc(0, 0, D, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    cr.restore()

    ink = (0.16, 0.10, 0.06)
    # red warning arc
    wf = spec.frac(spec.warn)
    cr.set_line_width(R * 0.05)
    cr.arc(0, 0, R * 0.685, START + SWEEP * wf, START + SWEEP)
    cr.set_source_rgba(0.62, 0.15, 0.10, 0.85); cr.stroke()
    # scale circle
    cr.set_line_width(R * 0.008); cr.set_source_rgb(*ink)
    cr.arc(0, 0, R * 0.715, START, START + SWEEP); cr.stroke()
    cr.arc(0, 0, R * 0.655, START, START + SWEEP); cr.stroke()
    # ticks
    nmaj = int(round((spec.vmax - spec.vmin) / spec.major))
    nmin = 5
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    for i in range(nmaj * nmin + 1):
        a = START + SWEEP * i / (nmaj * nmin)
        major = i % nmin == 0
        r0 = R * (0.585 if major else 0.655)
        r1 = R * 0.735 if major else R * 0.715
        cr.set_line_width(R * (0.024 if major else 0.009))
        cr.move_to(math.cos(a) * r0, math.sin(a) * r0); cr.line_to(math.cos(a) * r1, math.sin(a) * r1)
        cr.set_source_rgb(*ink); cr.stroke()
    # numerals
    _font(cr, SERIF, R * 0.105, bold=True)
    for i in range(nmaj + 1):
        a = START + SWEEP * i / nmaj
        v = spec.vmin + i * spec.major
        cr.set_source_rgb(*ink)
        _ctext(cr, _num(v), math.cos(a) * R * 0.475, math.sin(a) * R * 0.475)
    # stop pin
    a = START - math.radians(5)
    cr.arc(math.cos(a) * R * 0.62, math.sin(a) * R * 0.62, R * 0.018, 0, 2 * math.pi)
    cr.set_source_rgb(0.3, 0.22, 0.1); cr.fill()

    # engraved label + unit
    _font(cr, SERIF, R * 0.078, bold=True)
    cr.set_source_rgba(1, 0.97, 0.85, 0.7); _ctext(cr, spec.label, 0, -R * 0.285 + R * 0.006, spacing=R * 0.012)
    cr.set_source_rgb(0.22, 0.13, 0.07); _ctext(cr, spec.label, 0, -R * 0.285, spacing=R * 0.012)
    _font(cr, SERIF, R * 0.045, italic=True)
    cr.set_source_rgba(0.3, 0.2, 0.1, 0.9); _ctext(cr, spec.unit, 0, -R * 0.195, spacing=R * 0.004)
    # small ornament under the unit
    cr.set_line_width(R * 0.004); cr.set_source_rgba(0.3, 0.2, 0.1, 0.8)
    cr.move_to(-R * 0.12, -R * 0.155); cr.line_to(-R * 0.025, -R * 0.155); cr.stroke()
    cr.move_to(R * 0.025, -R * 0.155); cr.line_to(R * 0.12, -R * 0.155); cr.stroke()
    cr.arc(0, -R * 0.155, R * 0.009, 0, 2 * math.pi); cr.fill()

    # readout window (brass framed, dark enamel)
    ww, wh, wy = R * 0.44, R * 0.14, R * 0.47
    def rrect(x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()
    g = cairo.LinearGradient(0, wy - wh / 2 - R * 0.02, 0, wy + wh / 2 + R * 0.02)
    g.add_color_stop_rgb(0, 0.93, 0.80, 0.48); g.add_color_stop_rgb(0.5, 0.60, 0.42, 0.16); g.add_color_stop_rgb(1, 0.35, 0.23, 0.08)
    rrect(-ww / 2 - R * 0.02, wy - wh / 2 - R * 0.02, ww + R * 0.04, wh + R * 0.04, R * 0.035); cr.set_source(g); cr.fill()
    g = cairo.LinearGradient(0, wy - wh / 2, 0, wy + wh / 2)
    g.add_color_stop_rgb(0, 0.06, 0.04, 0.03); g.add_color_stop_rgb(1, 0.17, 0.11, 0.07)
    rrect(-ww / 2, wy - wh / 2, ww, wh, R * 0.022); cr.set_source(g); cr.fill()
    # maker's mark
    _font(cr, SERIF, R * 0.036, italic=True)
    cr.set_source_rgba(0.3, 0.2, 0.1, 0.75); _ctext(cr, "Omarchy & Cie · Paris", 0, R * 0.635)
    # screws on the bezel
    for a in (math.radians(-90), math.radians(90), math.radians(0), math.radians(180)):
        x, y = math.cos(a) * R * 0.887, math.sin(a) * R * 0.887
        gsw = cairo.RadialGradient(x - R * 0.006, y - R * 0.006, 0, x, y, R * 0.02)
        gsw.add_color_stop_rgb(0, 0.98, 0.9, 0.65); gsw.add_color_stop_rgb(1, 0.4, 0.27, 0.1)
        cr.arc(x, y, R * 0.018, 0, 2 * math.pi); cr.set_source(gsw); cr.fill()
        cr.set_line_width(R * 0.005); cr.set_source_rgba(0.2, 0.12, 0.04, 0.9)
        cr.move_to(x - R * 0.012, y - R * 0.004); cr.line_to(x + R * 0.012, y + R * 0.004); cr.stroke()
    surf.flush()
    return surf


def paint_dynamic(cr, spec, value, sub):
    """Needle, readout text, centre cap and glass reflection. cr origin = gauge centre."""
    R = spec.R
    # sub readout (engraved small typewriter text)
    if sub:
        _font(cr, MONO, R * 0.046, bold=True)
        cr.set_source_rgba(0.28, 0.17, 0.08, 0.85)
        _ctext(cr, sub, 0, R * 0.25)
    # digital readout
    txt = spec.missing if value is None else spec.fmt.format(value)
    _font(cr, MONO, R * 0.098, bold=True)
    cr.set_source_rgba(1.0, 0.55, 0.2, 0.18)     # faint glow
    _ctext(cr, txt, 0, R * 0.473)
    cr.set_source_rgb(0.98, 0.83, 0.52)
    _ctext(cr, txt, 0, R * 0.47)

    frac = spec.frac(value) if value is not None else None
    ang = START - math.radians(5) + math.radians(3) if frac is None else START + SWEEP * frac

    def needle_path():
        L, tail = R * 0.70, R * 0.20
        cr.move_to(-tail, 0)
        cr.line_to(-tail + R * 0.02, -R * 0.022)
        cr.line_to(R * 0.36, -R * 0.016)
        cr.line_to(R * 0.40, -R * 0.03)   # ornate lozenge
        cr.line_to(R * 0.46, 0 - R * 0.004)
        cr.line_to(L, -R * 0.0035)
        cr.line_to(L + R * 0.02, 0)
        cr.line_to(L, R * 0.0035)
        cr.line_to(R * 0.46, R * 0.004)
        cr.line_to(R * 0.40, R * 0.03)
        cr.line_to(R * 0.36, R * 0.016)
        cr.line_to(-tail + R * 0.02, R * 0.022)
        cr.close_path()
        cr.new_sub_path(); cr.arc(-tail + R * 0.01, 0, R * 0.045, 0, 2 * math.pi)   # counterweight

    # shadow
    cr.save(); cr.translate(R * 0.022, R * 0.035); cr.rotate(ang)
    needle_path(); cr.set_source_rgba(0, 0, 0, 0.30); cr.fill(); cr.restore()
    # needle (blued steel)
    cr.save(); cr.rotate(ang)
    needle_path()
    g = cairo.LinearGradient(0, -R * 0.03, 0, R * 0.03)
    g.add_color_stop_rgb(0, 0.30, 0.38, 0.62); g.add_color_stop_rgb(0.5, 0.10, 0.13, 0.26); g.add_color_stop_rgb(1, 0.05, 0.06, 0.12)
    cr.set_source(g); cr.fill()
    # hollow ring in the lozenge (Breguet-style)
    cr.arc(R * 0.40, 0, R * 0.013, 0, 2 * math.pi); cr.set_source_rgb(0.93, 0.86, 0.69); cr.fill()
    cr.arc(-R * 0.19, 0, R * 0.016, 0, 2 * math.pi); cr.set_source_rgba(0.93, 0.86, 0.69, 0.9); cr.fill()
    cr.restore()
    # centre cap
    g = cairo.RadialGradient(-R * 0.025, -R * 0.03, R * 0.005, 0, 0, R * 0.075)
    g.add_color_stop_rgb(0, 1.0, 0.94, 0.72); g.add_color_stop_rgb(0.5, 0.78, 0.58, 0.25); g.add_color_stop_rgb(1, 0.33, 0.21, 0.06)
    cr.arc(0, 0, R * 0.072, 0, 2 * math.pi); cr.set_source(g); cr.fill()
    cr.arc(0, 0, R * 0.072, 0, 2 * math.pi); cr.set_line_width(R * 0.006); cr.set_source_rgba(0.2, 0.12, 0.04, 0.9); cr.stroke()
    cr.arc(0, 0, R * 0.024, 0, 2 * math.pi); cr.set_source_rgb(0.24, 0.15, 0.05); cr.fill()
    # glass reflection
    D = R * 0.855
    cr.save()
    cr.arc(0, 0, D, 0, 2 * math.pi); cr.clip()
    cr.new_path()
    cr.arc(0, 0, D * 0.97, math.radians(160), math.radians(290))
    cr.arc_negative(D * 0.18, D * 0.22, D * 0.95, math.radians(285), math.radians(165))
    cr.close_path()
    g = cairo.LinearGradient(-D, -D, 0, 0)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.30); g.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.set_source(g); cr.fill()
    cr.restore()


class GaugeRenderer:
    """Holds the face cache for one gauge at one scale."""
    def __init__(self, spec):
        self.spec = spec
        self._face, self._scale = None, None

    def draw(self, cr, value, sub, scale):
        if self._face is None or abs(self._scale - scale) > 1e-3:
            self._face, self._scale = make_face(self.spec, scale), scale
        S = self.spec.size
        cr.set_source_surface(self._face, 0, 0)
        cr.paint()
        cr.save(); cr.translate(S / 2.0, S / 2.0)
        paint_dynamic(cr, self.spec, value, sub)
        cr.restore()

# --------------------------------------------------------------------------------------------
# Headless modes
# --------------------------------------------------------------------------------------------
def paint_background(cr, path, W, H):
    bg = None
    if path:
        try:
            bg = cairo.ImageSurface.create_from_png(path)
        except Exception:  # noqa: BLE001
            bg = None
    if bg is None:
        cr.set_source_rgb(0.35, 0.27, 0.2); cr.paint(); return
    bw, bh = bg.get_width(), bg.get_height()
    s = max(W / bw, H / bh)
    cr.save(); cr.translate((W - bw * s) / 2, (H - bh * s) / 2); cr.scale(s, s)
    cr.set_source_surface(bg, 0, 0); cr.get_source().set_filter(cairo.FILTER_GOOD); cr.paint(); cr.restore()


def render_png(out, W, H, scale, bg, fake, cfg):
    if cairo is None:
        sys.exit("python-cairo is required for --render-png")
    m = None if fake else Metrics(cfg)
    if m:
        time.sleep(1.0)
    data = ({"cpu": (4.7, "power", "RAPL PKG · 12% LOAD"), "disktemp": (38.0, "temp", "DRIVETEMP SDA"),
             "cputemp": (61.0, "temp", "CORETEMP PACKAGE"), "ram": (42.0, "pct", "6.5 / 15.4 GiB"),
             "diskload": (7.0, "pct", "SDA · 31% FULL")} if fake else m.sample())
    specs = make_specs(m)
    pw, ph = int(W * scale), int(H * scale)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, pw, ph)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    paint_background(cr, bg, W, H)
    for spec, cx, cy in layout(specs, W, H, 0):
        g = GaugeRenderer(spec)
        cr.save(); cr.translate(cx - spec.size / 2.0, cy - spec.size / 2.0)
        v, _, sub = data.get(spec.key, (None, None, ""))
        g.draw(cr, v, sub, scale)
        cr.restore()
    surf.write_to_png(out)
    print(f"wrote {out} ({pw}x{ph})")


def print_sensors(cfg, cfg_path):
    """Print what was detected and one set of live readings. Needs no GTK (and no cairo)."""
    m = Metrics(cfg)
    ps = m.power
    print(f"config            : {cfg_path or 'none (defaults)'}")
    print(f"disk              : {m.disk or '-'}  ({m.disk_how})")
    print(f"disk temp sensor  : {m.drive_temp_src or '-'}  ({m.drive_temp_path or 'not found'})")
    print(f"cpu temp sensor   : {m.int_temp_src or '-'}  ({m.int_temp_path or 'not found'})")
    if ps.present:
        where = ", ".join(U(p) for p, _w, _d in ps.zones)
        print(f"cpu power source  : {ps.kind}  ({where})  readable: {'yes' if m.power_ok else 'NO (permission?)'}")
    else:
        print("cpu power source  : none found")
    print(f"power scale       : 0-{m.power_scale:g} W  ({m.power_scale_how})")
    print(f"cpu gauge         : {'POWER' if m.show_power else 'LOAD'}  (cpu_gauge = {m.cpu_mode})")
    time.sleep(1.0)
    d = m.sample()
    print("readings:")
    for spec in make_specs(m):
        v, _kind, sub = d[spec.key]
        txt = spec.missing if v is None else spec.fmt.format(v)
        print(f"  {spec.label:10s} = {txt:>8s}   [{sub}]")


# --------------------------------------------------------------------------------------------
# GTK4 layer-shell wallpaper
# --------------------------------------------------------------------------------------------
def run_gtk(cfg):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    gi.require_foreign("cairo")
    from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell as LS

    bg = resolve_background(cfg["background"])
    if bg and not os.path.exists(bg):
        print(f"{APP_NAME}: background {bg} not found, using a transparent background", file=sys.stderr)
        bg = None
    interval = max(0.25, float(cfg["interval"]))
    layer_name = cfg["layer"]
    sel = cfg["monitors"]

    metrics = Metrics(cfg)
    state = {"data": {}, "shown": {}, "warned": False}
    windows = {}   # monitor -> (window, [(area, renderer)])

    css = Gtk.CssProvider()
    css.load_from_string("window.olw-wallpaper { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def draw_func(area, cr, w, h, rend):
        native = area.get_native()
        try:
            scale = native.get_surface().get_scale()
        except Exception:  # noqa: BLE001
            scale = float(area.get_scale_factor())
        v, _, sub = state["data"].get(rend.spec.key, (None, None, ""))
        rend.draw(cr, v, sub, scale)

    def make_window(app, monitor):
        geo = monitor.get_geometry()
        W, H = geo.width, geo.height
        win = Gtk.Window(application=app)
        win.add_css_class("olw-wallpaper")
        win.set_decorated(False)
        LS.init_for_window(win)
        LS.set_namespace(win, APP_NAME)
        LS.set_layer(win, {"background": LS.Layer.BACKGROUND, "bottom": LS.Layer.BOTTOM,
                           "top": LS.Layer.TOP, "overlay": LS.Layer.OVERLAY}[layer_name])
        LS.set_monitor(win, monitor)
        for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(win, edge, True)
        LS.set_exclusive_zone(win, -1)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        overlay = Gtk.Overlay()
        overlay.set_can_target(False)
        if bg:
            pic = Gtk.Picture.new_for_filename(bg)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_can_shrink(True)
            pic.set_can_target(False)
            overlay.set_child(pic)
        else:
            overlay.set_child(Gtk.Box())       # transparent: Omarchy's own wallpaper shows through
        fixed = Gtk.Fixed()
        fixed.set_can_target(False)
        overlay.add_overlay(fixed)
        gauges = []
        for spec, cx, cy in layout(make_specs(metrics), W, H, 0):
            area = Gtk.DrawingArea()
            area.set_content_width(spec.size); area.set_content_height(spec.size)
            area.set_can_target(False)
            rend = GaugeRenderer(spec)
            area.set_draw_func(draw_func, rend)
            fixed.put(area, int(cx - spec.size / 2), int(cy - spec.size / 2))
            gauges.append((area, rend))
        win.set_child(overlay)

        def on_realize(w):
            try:    # click-through: empty input region
                w.get_surface().set_input_region(cairo.Region())
            except Exception as e:  # noqa: BLE001
                print("input region:", e, file=sys.stderr)
        win.connect("realize", on_realize)
        win.present()
        return win, gauges

    def wanted(monitors):
        """Filter the GDK monitor list by the 'monitors' config value."""
        if sel == "all" or not monitors:
            return monitors
        if sel == "primary":
            return monitors[:1]          # Wayland has no "primary": use the first output (Hyprland id 0)
        names = [sel] if isinstance(sel, str) else [str(x) for x in sel]
        picked = [m for m in monitors if m.get_connector() in names]
        if not picked:
            if not state["warned"]:
                have = ", ".join(str(m.get_connector()) for m in monitors)
                print(f"{APP_NAME}: none of monitors={names} found (have: {have}); using the first one",
                      file=sys.stderr)
                state["warned"] = True
            return monitors[:1]
        return picked

    def sync_monitors(app):
        mons = Gdk.Display.get_default().get_monitors()
        current = wanted([mons.get_item(i) for i in range(mons.get_n_items())])
        for mon in list(windows):
            if mon not in current:
                windows.pop(mon)[0].destroy()
        for mon in current:
            if mon not in windows:
                windows[mon] = make_window(app, mon)
        return False

    def tick():
        state["data"] = metrics.sample()
        if metrics.show_power != state.get("show_power"):      # RAPL became (un)readable: swap the CPU gauge
            state["show_power"] = metrics.show_power
            for mon in list(windows):
                windows.pop(mon)[0].destroy()
            state["shown"].clear()
            sync_monitors(app)
            return True
        # redraw only the gauges whose visible state (text, needle step, sub-readout) changed
        for mon, (win, gauges) in windows.items():
            for area, rend in gauges:
                v, _, sub = state["data"].get(rend.spec.key, (None, None, ""))
                key = (id(area),)
                shown = (None if v is None else rend.spec.fmt.format(v),
                         round(rend.spec.frac(v) * 540) if v is not None else None, sub)
                if state["shown"].get(key) != shown:
                    state["shown"][key] = shown
                    area.queue_draw()
        return True

    def on_activate(app):
        if windows:
            return
        app.hold()
        state["data"] = metrics.sample()
        state["show_power"] = metrics.show_power
        sync_monitors(app)
        Gdk.Display.get_default().get_monitors().connect(
            "items-changed", lambda *a: GLib.timeout_add(500, sync_monitors, app))
        GLib.timeout_add(int(interval * 1000), tick)

    app = Gtk.Application(application_id="org.omarchy.LiveWallpaper")
    app.connect("activate", on_activate)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        sig_add = GLibUnix.signal_add
    except (ImportError, ValueError, AttributeError):
        sig_add = GLib.unix_signal_add
    for signum in (2, 15):
        sig_add(GLib.PRIORITY_DEFAULT, signum, lambda: (app.quit(), False)[1])
    return app.run([sys.argv[0]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="FILE", help=f"config file (default {config_path()})")
    ap.add_argument("--print-sensors", "--print-metrics", dest="print_sensors", action="store_true",
                    help="print detected sensors and current readings, then exit (no GTK needed)")
    ap.add_argument("--background", help="background image: path, 'default' or 'none' (overrides config)")
    ap.add_argument("--interval", type=float, help="refresh period in seconds (overrides config, default 1)")
    ap.add_argument("--disk", help="block device to monitor, e.g. nvme0n1 (overrides config)")
    ap.add_argument("--power-max", type=float, help="CPU POWER gauge full scale in watts (overrides config)")
    ap.add_argument("--monitors", help="'all', 'primary' or comma-separated connectors, e.g. DP-1,HDMI-A-1")
    ap.add_argument("--layer", choices=["background", "bottom", "top", "overlay"],
                    help="layer-shell layer (default bottom: above Omarchy's background, below windows)")
    ap.add_argument("--render-png", metavar="FILE", help="render one frame to a PNG and exit")
    ap.add_argument("--size", default="2150x900", help="logical size for --render-png")
    ap.add_argument("--scale", type=float, default=1.6, help="output scale for --render-png")
    ap.add_argument("--fake", action="store_true", help="use fake readings for --render-png")
    a = ap.parse_args()

    cfg, cfg_path = load_config(a.config)
    if a.background is not None:
        cfg["background"] = a.background
    if a.interval is not None:
        cfg["interval"] = max(0.25, a.interval)
    if a.disk:
        cfg["disk"] = a.disk
    if a.power_max is not None:
        cfg["power_max"] = a.power_max
    if a.monitors:
        cfg["monitors"] = a.monitors if a.monitors in ("all", "primary") else a.monitors.split(",")
    if a.layer:
        cfg["layer"] = a.layer

    if a.print_sensors:
        print_sensors(cfg, cfg_path); return 0
    if a.render_png:
        W, H = (int(x) for x in a.size.lower().split("x"))
        render_png(a.render_png, W, H, a.scale, resolve_background(cfg["background"]), a.fake, cfg); return 0
    if cairo is None:
        sys.exit("python-cairo is required (sudo pacman -S python-cairo)")
    return run_gtk(cfg)


if __name__ == "__main__":
    sys.exit(main() or 0)
