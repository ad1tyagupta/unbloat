"""A pretend phone for tests.

`FakeDevice.runner` stands in for the real adb binary. It parses the exact
command text unbloat would send to the phone's shell (with the same quoting
rules a POSIX shell uses) and answers from in-memory state, using command
output captured from real devices where format matters.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import shlex
from typing import Dict, List, Optional

from unbloat.adb import Adb, AdbTimeout, Result

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROOT = "/storage/emulated/0"
NOW = 1794891665
ALL_TOOLS = {"find", "stat", "du", "df", "sha256sum", "md5sum", "realpath", "readlink", "rm", "ls"}


def fixture(name: str, fname: str) -> str:
    with open(os.path.join(FIXTURES, name, fname), "r", encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def _pkgs(text: str) -> List[str]:
    return [l.split(":", 1)[1].split()[0] for l in text.splitlines() if l.startswith("package:")]


class FakeDevice:
    def __init__(self, serial: str = "FAKE123", fixture_name: str = "emu-api34"):
        self.serial = serial
        self.files: Dict[str, bytes] = {}
        self.mtimes: Dict[str, int] = {}
        self.dirs = {ROOT, ROOT + "/Android", ROOT + "/Android/data", ROOT + "/Download", ROOT + "/DCIM"}
        self.symlinks: Dict[str, str] = {}
        self.tools = set(ALL_TOOLS)
        self.find_printf = True
        self.props = {
            "ro.product.brand": "google",
            "ro.product.manufacturer": "Google",
            "ro.product.model": "Fake Phone",
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "sys.boot_completed": "1",
        }
        self.users = "Users:\n\tUserInfo{0:Owner:4c13} running\n"
        self.outputs: Dict[str, str] = {}
        self.packages: Dict[str, Dict[str, object]] = {}
        if fixture_name:
            self.outputs = {
                "diskstats": fixture(fixture_name, "diskstats.txt"),
                "usagestats": fixture(fixture_name, "usagestats.txt"),
                "device_policy": fixture(fixture_name, "device_policy.txt"),
            }
            system = set(_pkgs(fixture(fixture_name, "pm_system.txt")))
            off = set(_pkgs(fixture(fixture_name, "pm_disabled.txt")))
            for p in _pkgs(fixture(fixture_name, "pm_all.txt")):
                self.packages[p] = {"system": p in system, "enabled": p not in off, "installer": None}
        self.launcher: Optional[str] = "com.google.android.apps.nexuslauncher/.NexusLauncherActivity"
        self.settings = {
            ("secure", "default_input_method"): "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME",
        }
        self.roles = {
            "android.app.role.DIALER": "com.google.android.dialer",
            "android.app.role.SMS": "com.google.android.apps.messaging",
            "android.app.role.HOME": "com.google.android.apps.nexuslauncher",
        }
        self.role_cmd = "ok"
        self.systemui_pid = "888"
        self.break_on_disable: set = set()
        self.refuse_disable: set = set()
        self.corrupt_pull: set = set()
        self.undeletable: set = set()
        self.media_rows: set = set()
        self.offline_after: Optional[int] = None
        self.timeout_on: Optional[str] = None
        self.device_lines: Optional[str] = None
        self.free_blocks = 1000000
        self.trim_gain_blocks = 5000
        self.log: List[List[str]] = []
        self.raw_log: List[List[str]] = []
        self.calls = 0

    # -- helpers ----------------------------------------------------------
    def add_file(self, path: str, data: bytes = b"x", mtime: int = NOW - 1000) -> None:
        self.files[path] = data
        self.mtimes[path] = mtime
        d = posixpath.dirname(path)
        while d and d != "/" and d not in self.dirs:
            self.dirs.add(d)
            d = posixpath.dirname(d)

    def add_package(self, name: str, system: bool = True, enabled: bool = True) -> None:
        self.packages[name] = {"system": system, "enabled": enabled, "installer": None}

    def adb(self, **kw) -> Adb:
        return Adb(adb_path="adb", runner=self.runner, sleep=lambda _s: None, **kw)

    def shell_commands(self) -> List[str]:
        return [" ".join(c) for c in self.log]

    def _resolve(self, path: str) -> str:
        if path == "/sdcard" or path.startswith("/sdcard/"):
            path = ROOT + path[len("/sdcard"):]
        for link, target in self.symlinks.items():
            if path == link or path.startswith(link + "/"):
                return target + path[len(link):]
        return path

    # -- the adb binary ---------------------------------------------------
    def runner(self, argv: List[str], timeout: float) -> Result:
        self.raw_log.append(list(argv))
        args = argv[1:]
        if args[:1] == ["devices"]:
            text = self.device_lines
            if text is None:
                text = "List of devices attached\n%s\tdevice product:fake model:Fake_Phone device:fake transport_id:1\n" % self.serial
            return Result(argv, 0, text.encode(), b"")
        if args[:1] == ["version"]:
            return Result(argv, 0, b"Android Debug Bridge version 1.0.41\nVersion 35.0.2-fake\n", b"")
        if args[:1] == ["pair"]:
            ok = args[2] == "123456"
            return Result(argv, 0 if ok else 1, b"Successfully paired to x" if ok else b"Failed: Wrong password or connection was dropped.", b"")
        if args[:1] == ["connect"]:
            return Result(argv, 0, ("connected to %s" % args[1]).encode(), b"")
        assert args[0] == "-s", argv
        if args[1] != self.serial:
            return Result(argv, 1, b"", ("adb: device '%s' not found\n" % args[1]).encode())
        self.calls += 1
        if self.offline_after is not None and self.calls > self.offline_after:
            return Result(argv, 1, b"", b"adb: device offline\n")
        cmd = args[2:]
        if cmd[0] == "pull":
            return self._pull(argv, cmd[-2], cmd[-1])
        assert cmd[0] == "shell" and len(cmd) == 2, argv
        text = cmd[1]
        if self.timeout_on and self.timeout_on in text:
            raise AdbTimeout("The phone did not answer in time.", "")
        if text.startswith("for t in "):
            return Result(argv, 0, ("\n".join(sorted(self.tools)) + "\n").encode(), b"")
        parts = shlex.split(text)
        self.log.append(parts)
        out, err, code = self._shell(parts)
        if isinstance(out, str):
            out = out.encode("utf-8", "surrogateescape")
        return Result(argv, code, out, err.encode())

    def _pull(self, argv: List[str], remote: str, local: str) -> Result:
        remote = self._resolve(remote)
        if remote not in self.files:
            return Result(argv, 1, b"", b"adb: error: failed to stat remote object: No such file or directory\n")
        data = self.files[remote]
        if remote in self.corrupt_pull:
            data = bytes([data[0] ^ 0xFF]) + data[1:] if data else b"!"
        with open(local, "wb") as fh:
            fh.write(data)
        return Result(argv, 0, b"1 file pulled\n", b"")

    def _stat_line(self, path: str):
        if path == "/system/bin/sh":
            return "regular file|340704|1720722694"
        if path in self.symlinks:
            return "symbolic link|10|%d" % NOW
        real = self._resolve(path)
        if real in self.files:
            kind = "regular file" if self.files[real] else "regular empty file"
            return "%s|%d|%d" % (kind, len(self.files[real]), self.mtimes[real])
        if real in self.dirs:
            return "directory|4096|%d" % NOW
        return None

    def _shell(self, a: List[str]):
        c = a[0]
        if c not in ALL_TOOLS or c in self.tools:
            pass
        else:
            return "", "/system/bin/sh: %s: inaccessible or not found\n" % c, 127
        if c == "getprop":
            if len(a) == 1:
                return "".join("[%s]: [%s]\n" % kv for kv in self.props.items()), "", 0
            return self.props.get(a[1], "") + "\n", "", 0
        if c == "date":
            return ("%d\n" % NOW) if a[1] == "+%s" else "2026-11-17 10:31:05\n", "", 0
        if c == "pidof":
            return (self.systemui_pid + "\n") if self.systemui_pid else "", "", 0 if self.systemui_pid else 1
        if c == "settings":
            value = self.settings.get((a[2], a[3]), "null")
            if a[3] == "default_input_method" and not self.packages.get(value.split("/")[0], {"enabled": True})["enabled"]:
                value = ""  # Android clears the setting when the keyboard app is switched off
            return value + "\n", "", 0
        if c == "telecom":
            return self.outputs.get("telecom", "") , "", 0
        if c == "dumpsys":
            return self.outputs.get(a[1], ""), "", 0
        if c == "pm":
            return self._pm(a)
        if c == "cmd":
            return self._cmd(a)
        if c == "content":
            where = a[a.index("--where") + 1]
            path = where[len("_data='"):-1].replace("''", "'")
            if a[1] == "query":
                return ("Row: 0 _id=1\n" if path in self.media_rows else "No result found.\n"), "", 0
            self.media_rows.discard(path)
            return "", "", 0
        if c == "find":
            return self._find(a)
        if c == "stat":
            if a[1] == "-f":
                return "4096 2541783 %d %d\n" % (self.free_blocks, self.free_blocks), "", 0
            out, err = [], []
            for p in a[3:]:
                line = self._stat_line(p)
                if line is None:
                    err.append("stat: '%s': No such file or directory\n" % p)
                else:
                    out.append(line + "\n")
            return "".join(out), "".join(err), 1 if err else 0
        if c in ("sha256sum", "md5sum"):
            out = []
            for p in a[1:]:
                real = self._resolve(p)
                if real not in self.files:
                    return "".join(out), "%s: %s: No such file or directory\n" % (c, p), 1
                h = hashlib.sha256 if c == "sha256sum" else hashlib.md5
                out.append("%s  %s\n" % (h(self.files[real]).hexdigest(), p))
            return "".join(out), "", 0
        if c in ("realpath", "readlink"):
            paths = a[1:] if c == "realpath" else a[2:]
            out = []
            for p in paths:
                real = self._resolve(p)
                if real not in self.files and real not in self.dirs:
                    return "".join(out), "%s: %s: No such file or directory\n" % (c, p), 1
                out.append(real + "\n")
            return "".join(out), "", 0
        if c == "rm":
            for p in a[1:]:
                if p.startswith("-"):
                    continue
                real = self._resolve(p)
                if real in self.files and real not in self.undeletable:
                    del self.files[real]
            return "", "", 0
        if c == "ls":
            base = a[-1].rstrip("/")
            if base not in self.dirs:
                return "", "ls: %s: No such file or directory\n" % base, 1
            kids = {p[len(base) + 1:].split("/")[0] for p in list(self.files) + list(self.dirs) if p.startswith(base + "/")}
            return "".join(k + "\n" for k in sorted(kids)), "", 0
        if c == "df":
            return "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/fuse 10167132 560724 9458952 6% /storage/emulated\n", "", 0
        return "", "/system/bin/sh: %s: inaccessible or not found\n" % c, 127

    def _find(self, a: List[str]):
        root = a[1]
        if "-maxdepth" in a:
            return (b"340704\t1720722694.0\x00", "", 0) if self.find_printf else ("", "find: bad -printf\n", 1)
        if a[2:4] == ["-type", "d"]:
            return b"".join(d.encode("utf-8", "surrogateescape") + b"\x00" for d in sorted(self.dirs) if d.startswith(root)), "", 0
        names = sorted(p for p in self.files if p.startswith(root + "/"))
        if "-printf" in a:
            if not self.find_printf:
                return "", "find: bad -printf\n", 1
            return b"".join(
                b"%d\t%d.5\t%s\x00" % (len(self.files[p]), self.mtimes[p], p.encode("utf-8", "surrogateescape")) for p in names
            ), "", 0
        return b"".join(p.encode("utf-8", "surrogateescape") + b"\x00" for p in names), "", 0

    def _pm(self, a: List[str]):
        if a[1:3] == ["list", "users"]:
            return self.users, "", 0
        if a[1:3] == ["list", "packages"]:
            flags = set(a[3:])
            rows = []
            for name, st in self.packages.items():
                if "-s" in flags and not st["system"]:
                    continue
                if "-3" in flags and st["system"]:
                    continue
                if "-d" in flags and st["enabled"]:
                    continue
                rows.append("package:%s%s\n" % (name, "  installer=%s" % (st["installer"] or "null") if "-i" in flags else ""))
            return "".join(rows), "", 0
        if a[1] == "disable-user" and a[2:4] == ["--user", "0"]:
            pkg = a[4]
            if pkg not in self.packages:
                return "", "java.lang.IllegalArgumentException: Unknown package: %s\n" % pkg, 255
            if pkg in self.refuse_disable:
                return "", "java.lang.SecurityException: Cannot disable a protected package: %s\n" % pkg, 255
            self.packages[pkg]["enabled"] = False
            if pkg in self.break_on_disable:
                self.systemui_pid = ""
            return "Package %s new state: disabled-user\n" % pkg, "", 0
        if a[1] == "enable" and a[2:4] == ["--user", "0"]:
            pkg = a[4]
            if pkg in self.packages:
                self.packages[pkg]["enabled"] = True
            if not self.systemui_pid:
                self.systemui_pid = "999"
            return "Package %s new state: enabled\n" % pkg, "", 0
        if a[1] == "trim-caches":
            self.free_blocks += self.trim_gain_blocks
            return "", "", 0
        raise AssertionError("unbloat ran a pm command it must never run: %r" % (a,))

    def _cmd(self, a: List[str]):
        if a[1] == "package" and a[2] == "resolve-activity":
            if self.launcher and not self.packages.get(self.launcher.split("/")[0], {"enabled": True})["enabled"]:
                return "priority=0\ncom.android.settings/.FallbackHome\n", "", 0
            if not self.launcher:
                return "No activity found\n", "", 0
            return "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n%s\n" % self.launcher, "", 0
        if a[1] == "package" and a[2] == "query-activities":
            if not self.launcher:
                return "No activities found\n", "", 0
            return "1 activities found:\n  Activity #0:\n    priority=0\n    %s\n" % self.launcher, "", 0
        if a[1] == "role":
            if self.role_cmd == "unknown":
                return "Unknown command: get-role-holders\n", "", 255
            if self.role_cmd == "missing":
                return "", "cmd: Can't find service: role\n", 255
            return self.roles.get(a[-1], "") + "\n", "", 0
        if a[1] == "jobscheduler":
            return "Running job [FORCED]\n", "", 0
        return "", "cmd: unknown\n", 1
