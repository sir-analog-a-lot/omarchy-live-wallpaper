#!/usr/bin/env python3
"""Headless tests for sensor auto-detection, using small fake /sys + /proc trees.

Run:  python3 -m unittest discover -s tests -v      (no GTK, no cairo, no real hardware needed)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import omarchy_live_wallpaper as olw  # noqa: E402

PROC_STAT = "cpu  1000 0 500 8000 100 0 10 0 0 0\n"
MEMINFO = "MemTotal:       16000000 kB\nMemFree:         2000000 kB\nMemAvailable:    8000000 kB\n"


class Tree:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def f(self, path, content=""):
        p = self.root + path
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
        return p

    def d(self, path):
        os.makedirs(self.root + path, exist_ok=True)

    def ln(self, link, target):
        """Symlink link -> target, both absolute paths *inside* the fake tree."""
        p = self.root + link
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.symlink(self.root + target, p)

    def hwmon(self, n, name, device=None, temps=None):
        h = f"/sys/class/hwmon/hwmon{n}"
        self.f(h + "/name", name + "\n")
        if device:
            self.ln(h + "/device", device)
        for idx, (label, milli) in (temps or {}).items():
            if label:
                self.f(f"{h}/temp{idx}_label", label + "\n")
            self.f(f"{h}/temp{idx}_input", f"{milli}\n")

    def common(self, diskstats):
        self.f("/proc/stat", PROC_STAT)
        self.f("/proc/meminfo", MEMINFO)
        self.f("/proc/diskstats", diskstats)


def ds(name, ticks):
    # major minor name + 11 fields; field index 12 = io_ticks
    return f"   8 0 {name} 1 0 0 0 0 0 0 0 0 {ticks} 0\n"


def nvme_amd_tree():
    """Omarchy default: LUKS + btrfs on NVMe, AMD Ryzen (k10temp, RAPL via powercap, no constraints)."""
    t = Tree()
    pci = "/sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0"
    ctrl = pci + "/nvme/nvme0"
    t.f("/proc/self/mountinfo", "22 1 0:25 /@ / rw,relatime shared:1 - btrfs /dev/mapper/root rw\n")
    t.d("/dev")
    t.f("/dev/dm-0")
    t.ln("/dev/mapper/root", "/dev/dm-0")
    t.d("/sys/devices/virtual/block/dm-0/slaves/nvme0n1p2")
    t.ln("/sys/class/block/dm-0", "/sys/devices/virtual/block/dm-0")
    t.d("/sys/block/dm-0")
    t.f(ctrl + "/nvme0n1/nvme0n1p2/partition", "2\n")
    t.ln("/sys/class/block/nvme0n1p2", ctrl + "/nvme0n1/nvme0n1p2")
    t.ln("/sys/block/nvme0n1", ctrl + "/nvme0n1")
    t.ln(ctrl + "/nvme0n1/device", ctrl)
    t.d("/sys/block/loop0")
    t.d("/sys/block/zram0")
    t.hwmon(0, "nvme", ctrl, {1: ("Composite", 41850), 2: ("Sensor 1", 39850)})
    t.hwmon(1, "k10temp", pci.replace("01.1/0000:01:00.0", "18.3"), {1: ("Tctl", 55125), 3: ("Tccd1", 50000)})
    t.hwmon(2, "acpitz", None, {1: (None, 30000)})
    rz = "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0"
    t.f(rz + "/name", "package-0\n")
    t.f(rz + "/energy_uj", "123456789\n")
    t.f(rz + "/max_energy_range_uj", "65532610987\n")
    t.f(rz + "/intel-rapl:0:0/name", "core\n")
    t.ln("/sys/class/powercap/intel-rapl:0", rz)
    t.ln("/sys/class/powercap/intel-rapl:0:0", rz + "/intel-rapl:0:0")
    t.common(ds("nvme0n1", 5000) + ds("nvme0n1p2", 4900) + ds("dm-0", 4800) + ds("loop0", 99999) + ds("zram0", 88888))
    return t


def sata_intel_tree(drivetemp=True, rapl_readable=True):
    """Intel N95-style box: LUKS on sda2, coretemp, RAPL with 15 W limit, drivetemp optional."""
    t = Tree()
    scsi = "/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1:0:0/1:0:0:0"
    t.f("/proc/self/mountinfo", "22 1 0:25 /@ / rw - btrfs /dev/mapper/root rw\n")
    t.f("/dev/dm-0")
    t.ln("/dev/mapper/root", "/dev/dm-0")
    t.d("/sys/devices/virtual/block/dm-0/slaves/sda2")
    t.ln("/sys/class/block/dm-0", "/sys/devices/virtual/block/dm-0")
    t.f(scsi + "/block/sda/sda2/partition", "2\n")
    t.ln("/sys/class/block/sda2", scsi + "/block/sda/sda2")
    t.ln("/sys/block/sda", scsi + "/block/sda")
    t.ln(scsi + "/block/sda/device", scsi)
    t.hwmon(0, "coretemp", "/sys/devices/platform/coretemp.0",
            {1: ("Package id 0", 47000), 2: ("Core 0", 45000)})
    if drivetemp:
        t.hwmon(1, "drivetemp", scsi, {1: (None, 38000)})
    rz = "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0"
    t.f(rz + "/name", "package-0\n")
    e = t.f(rz + "/energy_uj", "5000000\n")
    if not rapl_readable:
        os.chmod(e, 0)
    t.f(rz + "/constraint_0_max_power_uw", "15000000\n")
    t.f(rz + "/constraint_0_power_limit_uw", "15000000\n")
    t.ln("/sys/class/powercap/intel-rapl:0", rz)
    t.common(ds("sda", 100) + ds("sda2", 90) + ds("dm-0", 80))
    return t


class DetectionTests(unittest.TestCase):
    def use(self, tree):
        self.tree = tree
        olw.SYSROOT = tree.root

    def tearDown(self):
        olw.SYSROOT = ""
        self.tree.tmp.cleanup()

    def test_nvme_amd(self):
        self.use(nvme_amd_tree())
        m = olw.Metrics(dict(olw.DEFAULTS))
        self.assertEqual((m.disk, m.disk_how), ("nvme0n1", "root filesystem"))
        self.assertTrue(m.drive_temp_path.endswith("hwmon0/temp1_input"))
        self.assertEqual(m.drive_temp_src, "NVME0N1 COMPOSITE")
        self.assertTrue(m.int_temp_path.endswith("hwmon1/temp1_input"))
        self.assertEqual(m.int_temp_src, "K10TEMP TCTL")
        self.assertEqual(m.power.kind, "rapl")
        self.assertEqual(len(m.power.zones), 1)            # sub-zone intel-rapl:0:0 ignored
        self.assertTrue(m.power_ok and m.show_power)
        self.assertEqual(m.power_scale_how.split()[0], "guess")   # AMD powercap has no constraints
        d = m.sample()
        self.assertAlmostEqual(d["disktemp"][0], 41.85)
        self.assertAlmostEqual(d["cputemp"][0], 55.125)
        self.assertAlmostEqual(d["ram"][0], 50.0)

    def test_sata_intel(self):
        self.use(sata_intel_tree())
        m = olw.Metrics(dict(olw.DEFAULTS))
        self.assertEqual(m.disk, "sda")
        self.assertEqual(m.drive_temp_src, "DRIVETEMP SDA")
        self.assertEqual(m.int_temp_src, "CORETEMP PACKAGE")
        self.assertEqual(m.power_scale, 15.0)
        self.assertIn("RAPL limit 15", m.power_scale_how)

    def test_sata_without_drivetemp_or_rapl_access(self):
        if os.geteuid() == 0:
            self.skipTest("root can read chmod-000 files")
        self.use(sata_intel_tree(drivetemp=False, rapl_readable=False))
        m = olw.Metrics(dict(olw.DEFAULTS))
        self.assertIsNone(m.drive_temp_path)
        self.assertFalse(m.power_ok)
        self.assertFalse(m.show_power)                      # auto -> CPU LOAD gauge
        d = m.sample()
        self.assertIsNone(d["disktemp"][0])
        self.assertEqual(d["disktemp"][2], "MODPROBE DRIVETEMP")
        self.assertEqual([s.label for s in olw.make_specs(m)][2], "CPU LOAD")
        forced = olw.Metrics(dict(olw.DEFAULTS, cpu_gauge="power"))
        d = forced.sample()
        self.assertIsNone(d["cpu"][0])
        self.assertEqual(d["cpu"][2], "NO RAPL ACCESS")
        spec = olw.make_specs(forced)[2]
        self.assertEqual((spec.label, spec.missing), ("CPU POWER", "N/A"))

    def test_overrides(self):
        self.use(sata_intel_tree())
        m = olw.Metrics(dict(olw.DEFAULTS, disk="/dev/sdb", power_max=35))
        self.assertEqual((m.disk, m.disk_how), ("sdb", "config"))
        self.assertEqual((m.power_scale, m.power_scale_how), (35.0, "config"))
        # sdb was chosen explicitly and has no sensor: don't show another drive's temperature
        self.assertIsNone(m.drive_temp_path)
        self.assertEqual(m.sample()["disktemp"][2], "MODPROBE DRIVETEMP")

    def test_auto_disk_falls_back_to_other_drive_sensor(self):
        t = nvme_amd_tree()
        # NVMe hwmon not linked to the root disk (e.g. unusual sysfs layout): still used, but labelled
        os.unlink(t.root + "/sys/class/hwmon/hwmon0/device")
        self.use(t)
        m = olw.Metrics(dict(olw.DEFAULTS))
        self.assertEqual(m.drive_temp_src, "NVME (NOT ROOT)")

    def test_busiest_disk_fallback(self):
        t = nvme_amd_tree()
        t.f("/proc/self/mountinfo", "22 1 0:25 / / rw - overlay overlay rw\n")
        self.use(t)
        disk, how = olw.pick_disk("auto")
        self.assertEqual((disk, how), ("nvme0n1", "busiest disk"))   # loop0/zram0 are skipped

    def test_config_file(self):
        self.tree = Tree()
        p = self.tree.f("/config.toml", 'disk = "nvme1n1"\ninterval = 0.1\nmonitors = ["DP-1"]\nlayer = "nope"\n')
        cfg, used = olw.load_config(p, warn=False)
        self.assertEqual(used, p)
        self.assertEqual(cfg["disk"], "nvme1n1")
        self.assertEqual(cfg["interval"], 0.25)          # clamped
        self.assertEqual(cfg["monitors"], ["DP-1"])
        self.assertEqual(cfg["layer"], "bottom")         # invalid -> default
        bad = self.tree.f("/bad.toml", "disk = \n")
        cfg, used = olw.load_config(bad, warn=False)
        self.assertIsNone(used)
        self.assertEqual(cfg, olw.DEFAULTS)
        cfg, used = olw.load_config(self.tree.root + "/missing.toml")
        self.assertIsNone(used)

    def test_power_tiers(self):
        class Fake:
            def __init__(self, w): self.w = w
            def limit_watts(self): return self.w
        self.tree = Tree()
        self.assertEqual(olw.power_scale(Fake(6))[0], 15)
        self.assertEqual(olw.power_scale(Fake(28))[0], 35)
        self.assertEqual(olw.power_scale(Fake(65))[0], 65)
        self.assertEqual(olw.power_scale(Fake(105))[0], 125)
        self.assertEqual(olw.power_scale(Fake(900))[0], 500)


if __name__ == "__main__":
    unittest.main()
