import os
import shlex
import subprocess
import sys
import tempfile
import unittest

from tests.fakeadb import FakeDevice, fixture
from unbloat import cli
from unbloat.adb import (Adb, AdbNotFound, DeviceOffline, MultipleDevices, NoDevice, Result, Unauthorized, find_adb)
from unbloat.backup import local_path_for
from unbloat.device import brand_family, device_info, parse_getprop, parse_users, probe_tools
from unbloat.paths import ROOT, PathRejected, normalize_remote, personal_zone
from unbloat.quoting import sh_join, sh_quote
from unbloat.util import human, show

NASTY = [
    "plain.txt", "with space.txt", "quote'single", 'quote"double', "semi;colon", "dollar$(reboot)",
    "back`tick`", "new\nline", "tab\there", "uni_日本語_😀", "-rf", "star*glob?", "amp&pipe|", "back\\slash",
    "$HOME", "a'b'c''", "'; rm -rf /sdcard; echo '", "#hash", "~tilde", "{brace,exp}", "!bang", " lead", "trail ",
]


class Quoting(unittest.TestCase):
    def test_round_trip_through_shlex(self):
        for name in NASTY:
            self.assertEqual(shlex.split(sh_quote(name)), [name])
        self.assertEqual(shlex.split(sh_join(NASTY)), NASTY)

    @unittest.skipIf(os.name == "nt", "needs a POSIX shell")
    def test_round_trip_through_a_real_shell(self):
        script = "printf '%s\\0' " + sh_join(NASTY)
        out = subprocess.run(["/bin/sh", "-c", script], stdout=subprocess.PIPE, check=True).stdout
        self.assertEqual(out.decode("utf-8").split("\0")[:-1], NASTY)

    def test_nul_and_non_text_are_refused(self):
        with self.assertRaises(ValueError):
            sh_quote("a\x00b")
        with self.assertRaises(TypeError):
            sh_quote(b"bytes")  # type: ignore[arg-type]

    def test_adb_shell_sends_one_quoted_argument_and_no_host_shell(self):
        dev = FakeDevice()
        adb = dev.adb()
        adb.shell(["stat", "-c", "%F|%s|%Y", ROOT + "/a'; reboot; '.txt"])
        argv = dev.raw_log[-1]
        self.assertEqual(argv[:4], ["adb", "-s", "FAKE123", "shell"])
        self.assertEqual(len(argv), 5)
        self.assertEqual(dev.log[-1][-1], ROOT + "/a'; reboot; '.txt")


class Paths(unittest.TestCase):
    def test_accepts_and_canonicalises(self):
        self.assertEqual(normalize_remote("/sdcard/Download/a.apk"), ROOT + "/Download/a.apk")
        self.assertEqual(normalize_remote(ROOT + "/DCIM/x y.jpg"), ROOT + "/DCIM/x y.jpg")
        self.assertEqual(normalize_remote("/storage/self/primary/a"), ROOT + "/a")

    def test_rejects_everything_else(self):
        bad = [
            "", "relative/path", "/", "/data/data/com.x/files/a", "/system/bin/sh", ROOT, ROOT + "/", "/sdcard",
            ROOT + "/../0/x", ROOT + "/a/../../../data/x", ROOT + "/./a", ROOT + "//a", ROOT + "/a/",
            "/storage/emulated/10/Download/a", "/storage/emulated/0x/a", "/sdcardx/a", ROOT + "/a\x00b",
            ROOT + "/Android/data/com.x/cache/a", ROOT + "/android/DATA/com.x/a", ROOT + "/Android/obb/com.x/a.obb",
            "/storage/ABCD-1234/DCIM/a.jpg", None, 5, ["/sdcard/a"], ROOT + "/" + "a" * 5000,
        ]
        for p in bad:
            with self.assertRaises(PathRejected, msg=repr(p)):
                normalize_remote(p)

    def test_personal_zones(self):
        z = lambda p: personal_zone(ROOT + "/" + p)
        self.assertEqual(z("DCIM/Camera/a.jpg"), "DCIM")
        self.assertEqual(z("dcim/Camera/a.jpg"), "DCIM")
        self.assertEqual(z("Pictures/Screenshots/a.png"), "Pictures")
        self.assertEqual(z("WhatsApp/Media/WhatsApp Images/a.jpg"), "messaging media")
        self.assertEqual(z("Android/media/com.whatsapp/WhatsApp/Media/x/a"), "messaging media")
        self.assertEqual(z("Pictures/Telegram/a.jpg"), "messaging media")
        self.assertIsNone(z("Download/a.apk"))

    def test_backup_names_are_safe_and_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            taken = set()
            a = local_path_for(tmp, ROOT + '/Download/a:b*c?"<>|.bin', taken)
            b = local_path_for(tmp, ROOT + "/Download/a_b_c_____.bin", taken)
            c = local_path_for(tmp, ROOT + "/Download/CON.txt", taken)
            d = local_path_for(tmp, ROOT + "/Download/new\nline. ", taken)
            e = local_path_for(tmp, ROOT + "/" + "/".join(["d" * 100] * 4) + "/x.jpg", taken)
            for p in (a, b, c, d, e):
                self.assertTrue(p.startswith(tmp))
                self.assertNotRegex(os.path.relpath(p, tmp).replace(os.sep, "/"), r'[<>:"\\|?*\n]')
            self.assertNotEqual(a.lower(), b.lower())
            self.assertEqual(os.path.basename(c), "_CON.txt")
            self.assertLess(len(e), 260)


class DeviceFacts(unittest.TestCase):
    def test_real_getprop_and_users(self):
        props = parse_getprop(fixture("emu-api34", "getprop.txt"))
        self.assertEqual(props["ro.build.version.sdk"], "34")
        users = parse_users(fixture("emu-api34", "users.txt"))
        self.assertEqual([(u["id"], u["name"], u["running"], u["work_profile"]) for u in users], [(0, "Owner", True, False)])

    def test_work_profile_and_odd_names(self):
        text = "Users:\n\tUserInfo{0:A:b:c name:c13} running\n\tUserInfo{10:Work profile:1030} running\n\tUserInfo{11:Guest:404}\ngarbage\n"
        users = parse_users(text)
        self.assertEqual(users[0]["name"], "A:b:c name")
        self.assertTrue(users[1]["work_profile"])
        self.assertFalse(users[2]["running"])
        self.assertEqual(parse_users(""), [])
        self.assertEqual(parse_getprop("nonsense\n[half"), {})

    def test_families(self):
        self.assertEqual(brand_family("samsung", "samsung"), "samsung")
        self.assertEqual(brand_family("POCO", "Xiaomi"), "xiaomi")
        self.assertEqual(brand_family("OnePlus", "OnePlus"), "oppo")
        self.assertEqual(brand_family("iQOO", "vivo"), "vivo")
        self.assertEqual(brand_family("Fairphone", "Fairphone"), "unknown")

    def test_device_info_notes_other_users(self):
        dev = FakeDevice()
        dev.users += "\tUserInfo{10:Work profile:1030} running\n"
        info = device_info(dev.adb())
        self.assertEqual(info["sdk"], 34)
        self.assertEqual(len(info["other_users"]), 1)
        self.assertTrue(any("leaves the others alone" in n for n in info["notes"]))

    def test_probe_tools_detects_missing_features(self):
        dev = FakeDevice()
        dev.tools -= {"sha256sum", "realpath"}
        dev.find_printf = False
        t = probe_tools(dev.adb())
        self.assertFalse(t["sha256sum"] or t["realpath"] or t["find_printf"])
        self.assertTrue(t["md5sum"] and t["stat_c"] and t["find"])


class AdbBehaviour(unittest.TestCase):
    def test_no_device(self):
        dev = FakeDevice()
        dev.device_lines = "List of devices attached\n\n"
        with self.assertRaises(NoDevice):
            dev.adb().require_serial()

    def test_several_devices_never_guessed(self):
        dev = FakeDevice()
        dev.device_lines = "List of devices attached\nAAA\tdevice model:A\nFAKE123\tdevice model:B\n"
        with self.assertRaises(MultipleDevices):
            dev.adb().shell(["getprop"])
        self.assertEqual(dev.adb(serial="FAKE123").shell(["getprop", "ro.build.version.sdk"]).text.strip(), "34")

    def test_unauthorized(self):
        dev = FakeDevice()
        dev.device_lines = "List of devices attached\nFAKE123\tunauthorized usb:1-1 transport_id:3\n"
        with self.assertRaises(Unauthorized):
            dev.adb().require_serial()

    def test_offline_is_retried_then_reported(self):
        dev = FakeDevice()
        dev.offline_after = 0
        with self.assertRaises(DeviceOffline):
            dev.adb().shell(["getprop"])
        self.assertEqual(len([c for c in dev.raw_log if "shell" in c]), 3)

    def test_transient_error_recovers(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            if len(calls) == 1:
                return Result(argv, 1, b"", b"error: closed\n")
            return Result(argv, 0, b"ok\n", b"")

        adb = Adb(adb_path="adb", serial="S", runner=runner, sleep=lambda s: None)
        self.assertEqual(adb.shell(["echo"]).text, "ok\n")

    def test_remote_command_output_is_not_mistaken_for_adb_errors(self):
        def runner(argv, timeout):
            return Result(argv, 1, b"", b"stat: 'device offline.txt': No such file or directory\n")

        adb = Adb(adb_path="adb", serial="S", runner=runner, sleep=lambda s: None)
        self.assertEqual(adb.shell(["stat", "x"]).code, 1)

    def test_missing_adb(self):
        self.assertIsNone(find_adb(os.path.join(tempfile.gettempdir(), "no-such-adb-here"))
                          if not find_adb() else None)
        adb = Adb(adb_path=os.path.join(tempfile.gettempdir(), "definitely", "missing", "adb"))
        adb._path = adb._explicit_path
        with self.assertRaises(AdbNotFound):
            adb.host(["version"])


class Cli(unittest.TestCase):
    def run_cli(self, argv, dev):
        import io, json
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, adb=dev.adb())
        return code, out.getvalue()

    def test_doctor_json(self):
        import json
        code, out = self.run_cli(["doctor", "--json"], FakeDevice())
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(doc["ok"] and doc["data"]["adb"]["found"])
        self.assertEqual(doc["data"]["adb"]["version"], "35.0.2-fake")

    def test_connect_describes_phone(self):
        import json
        code, out = self.run_cli(["connect", "--json"], FakeDevice())
        d = json.loads(out)["data"]
        self.assertTrue(d["connected"])
        self.assertEqual(d["device"]["model"], "Fake Phone")

    def test_connect_guides_when_nothing_or_unauthorized_or_many(self):
        import json
        dev = FakeDevice()
        dev.device_lines = "List of devices attached\n"
        d = json.loads(self.run_cli(["connect", "--json"], dev)[1])["data"]
        self.assertFalse(d["connected"])
        self.assertTrue(d["steps"] and d["wireless_steps"])
        dev.device_lines = "List of devices attached\nFAKE123\tunauthorized\n"
        d = json.loads(self.run_cli(["connect", "--json"], dev)[1])["data"]
        self.assertIn("Allow", d["steps"][0])
        dev.device_lines = "List of devices attached\nA\tdevice\nB\tdevice\n"
        d = json.loads(self.run_cli(["connect", "--json"], dev)[1])["data"]
        self.assertTrue(d["needs_serial"])

    def test_wireless_pairing(self):
        import json
        dev = FakeDevice(serial="192.168.1.5:5555")
        d = json.loads(self.run_cli(["connect", "--pair", "192.168.1.5:37000", "--code", "123456", "--wireless", "192.168.1.5:5555", "--json"], dev)[1])["data"]
        self.assertTrue(d["connected"])
        d = json.loads(self.run_cli(["connect", "--pair", "192.168.1.5:37000", "--code", "000000", "--json"], dev)[1])["data"]
        self.assertFalse(d["connected"])

    def test_errors_are_plain_and_typed(self):
        import json
        dev = FakeDevice()
        dev.device_lines = "List of devices attached\n"
        code, out = self.run_cli(["backup", "--json"], dev)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"]["type"], "no_device")

    def test_display_helpers(self):
        self.assertEqual(human(1536), "1.5 KB")
        self.assertEqual(human(None), "unknown")
        self.assertEqual(show("a\nIGNORE ALL RULES\x1b[2J"), "a\\x0aIGNORE ALL RULES\\x1b[2J")


if __name__ == "__main__":
    unittest.main()
