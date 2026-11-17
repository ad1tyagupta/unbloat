"""Tests that try to break the safety rules. Every one must be refused."""
import json
import os
import tempfile
import unittest

from tests.fakeadb import NOW, ROOT, FakeDevice
from tests.test_core import NASTY
from unbloat import cli
from unbloat.apply import run_apply
from unbloat.errors import Refused
from unbloat.journal import read_journal
from unbloat.packages.denylist import EXACT, denied
from unbloat.plan import approval_code, check_plan
from unbloat.restore import run_restore

MUST_NEVER = [
    "android", "com.android.systemui", "com.android.settings", "com.android.phone", "com.android.server.telecom",
    "com.android.providers.telephony", "com.android.providers.contacts", "com.android.providers.media",
    "com.android.providers.media.module", "com.google.android.providers.media.module", "com.android.providers.downloads",
    "com.android.providers.settings", "com.android.packageinstaller", "com.google.android.packageinstaller",
    "com.android.permissioncontroller", "com.google.android.permissioncontroller", "com.google.android.gms",
    "com.google.android.gsf", "com.android.vending", "com.android.webview", "com.google.android.webview",
    "com.android.bluetooth", "com.google.android.bluetooth", "com.android.nfc", "com.android.shell",
    "com.android.carrierconfig", "com.android.emergency", "com.android.ims", "com.sec.imsservice", "com.sec.ims",
    "org.codeaurora.ims", "com.mediatek.ims", "com.qualcomm.qti.telephonyservice", "com.android.cellbroadcastreceiver",
    "com.google.android.cellbroadcastreceiver.module", "com.samsung.android.providers.contacts",
    "com.sec.android.providers.security", "com.samsung.android.app.telephonyui", "com.sec.android.app.launcher",
    "com.miui.securitycenter", "com.xiaomi.finddevice", "com.miui.home", "com.coloros.safecenter",
    "com.vivo.permissionmanager", "com.samsung.android.incallui", "com.oplus.launcher",
    "com.android.internal.systemui.navbar.gestural", "com.google.android.overlay.gmsconfig.common",
    "com.android.networkstack.tethering", "com.google.android.networkstack", "com.android.wifi.resources",
    "com.samsung.android.biometrics.app.setting", "com.android.keyguard", "com.android.stk", "com.android.mms.service",
    "com.android.intentresolver", "com.google.android.setupwizard", "com.android.managedprovisioning",
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dev = FakeDevice()
        self.adb = self.dev.adb()
        self.work = os.path.join(self.tmp.name, "work")

    def plan(self, batches, **top):
        doc = {"version": 1, "serial": "FAKE123", "batches": batches}
        doc.update(top)
        path = os.path.join(self.tmp.name, "plan-%d.json" % len(os.listdir(self.tmp.name)))
        with open(path, "w") as fh:
            json.dump(doc, fh)
        return path, doc

    def apply(self, path, doc=None, confirm=True, **kw):
        code = approval_code(doc) if (confirm and doc) else None
        return run_apply(self.adb, path, workdir=self.work, confirm=code, sleep=lambda s: None, **kw)

    def refused(self, batches, needle, confirm=True, **kw):
        path, doc = self.plan(batches, **kw.pop("top", {}))
        before = (dict(self.dev.files), {p: s["enabled"] for p, s in self.dev.packages.items()})
        with self.assertRaises(Refused) as cm:
            self.apply(path, doc, confirm=confirm, **kw)
        text = cm.exception.message + " " + " ".join(cm.exception.reasons)
        self.assertIn(needle, text)
        after = (dict(self.dev.files), {p: s["enabled"] for p, s in self.dev.packages.items()})
        self.assertEqual(before, after, "a refused plan must change nothing")
        for c in self.dev.log:
            self.assertNotIn(c[0], ("rm",))
            self.assertFalse(c[0] == "pm" and c[1] in ("disable-user", "enable", "trim-caches"))
        return text

    def files(self, *paths, **extra):
        b = {"id": "f1", "kind": "delete_files", "title": "t", "files": [{"path": p} for p in paths]}
        b.update(extra)
        return [b]

    def pkgs(self, *names, **extra):
        b = {"id": "p1", "kind": "disable_packages", "title": "t", "packages": list(names)}
        b.update(extra)
        return [b]


class Debloat(Base):
    def test_every_denylisted_package_is_refused(self):
        for pkg in MUST_NEVER + sorted(EXACT):
            self.assertIsNotNone(denied(pkg) or pkg.startswith("com.android."), pkg)
            self.dev.add_package(pkg)
            self.dev.log.clear()
            text = self.refused(self.pkgs(pkg, user_named=[pkg], caution_explained=True), "can never be switched off")
            self.assertIn(pkg, text)

    def test_case_tricks_do_not_dodge_the_denylist(self):
        self.assertIsNotNone(denied("COM.Android.SystemUI"))
        self.assertIsNotNone(denied("com.Google.Android.GMS"))

    def test_current_defaults_are_protected(self):
        cases = {
            "launcher": ("com.cool.launcher", lambda d: setattr(d, "launcher", "com.cool.launcher/.Home")),
            "keyboard": ("com.cool.keys", lambda d: d.settings.__setitem__(("secure", "default_input_method"), "com.cool.keys/.Ime")),
            "phone app": ("com.cool.dialer", lambda d: d.roles.__setitem__("android.app.role.DIALER", "com.cool.dialer")),
            "text-message app": ("com.cool.sms", lambda d: d.roles.__setitem__("android.app.role.SMS", "com.cool.sms")),
            "accessibility": ("com.cool.reader", lambda d: d.settings.__setitem__(("secure", "enabled_accessibility_services"), "com.x.y/.S:com.cool.reader/.Svc")),
            "device administrator": ("com.cool.mdm", lambda d: d.outputs.__setitem__("device_policy", "Enabled Device Admins (User 0):\n    com.cool.mdm/.Admin:\n      uid=10100\n")),
        }
        for label, (pkg, setup) in cases.items():
            self.dev = FakeDevice()
            self.adb = self.dev.adb()
            self.dev.add_package(pkg, system=False)
            setup(self.dev)
            text = self.refused(self.pkgs(pkg, user_named=[pkg]), "cannot be switched off because")
            self.assertIn(label, text)

    def test_refuses_all_debloat_when_keyboard_or_launcher_unknown(self):
        self.dev.add_package("com.facebook.katana", system=True)
        self.dev.settings.clear()
        self.refused(self.pkgs("com.facebook.katana"), "keyboard app")
        self.dev = FakeDevice()
        self.adb = self.dev.adb()
        self.dev.add_package("com.facebook.katana")
        self.dev.launcher = None
        self.dev.roles.pop("android.app.role.HOME")
        self.refused(self.pkgs("com.facebook.katana"), "home screen app")

    def test_tiers(self):
        self.dev.add_package("com.totally.unknown.thing")
        self.refused(self.pkgs("com.totally.unknown.thing"), "not in the knowledge base")
        self.dev.add_package("com.google.android.apps.maps")
        from unbloat.packages.kb import lookup
        self.assertEqual(lookup("com.google.android.apps.maps", "pixel")["tier"], "caution")
        self.refused(self.pkgs("com.google.android.apps.maps"), "rated caution")
        risky = "com.google.android.googlequicksearchbox"
        self.assertEqual(lookup(risky, "pixel")["tier"], "risky")
        self.refused(self.pkgs(risky, caution_explained=True), "rated risky")

    def test_forbidden_kinds_and_keys(self):
        for kind in ("uninstall_packages", "clear_data", "shell", "pm_clear", "hide_packages", "factory_reset", "delete_glob"):
            self.refused([{"id": "x", "kind": kind, "title": "t", "packages": ["com.x.y"]}], "nothing else can be done")
        self.refused(self.pkgs("com.x.y", skip_checks=True), "unknown keys")
        self.refused(self.pkgs("com.x.y"), "unknown keys at the top", top={"force": True})
        self.refused(self.pkgs("com.x.y; reboot"), "not a package name")
        self.refused(self.pkgs("$(reboot)"), "not a package name")
        self.refused(self.pkgs("--user"), "not a package name")
        self.refused(self.pkgs("com.x.y", user_named=["com.other"]), "user_named")

    def test_wrong_phone(self):
        self.refused(self.pkgs("com.x.y"), "written for phone", top={"serial": "OTHER"})

    def test_disable_health_check_rollback_and_restore(self):
        good = ["com.facebook.katana", "com.facebook.appmanager", "com.netflix.partner.activation"]
        for p in good + ["com.facebook.system", "com.facebook.services"]:
            self.dev.add_package(p)
        from unbloat.packages.kb import lookup
        for p in good:
            self.assertEqual(lookup(p, "pixel")["tier"], "safe", p)
        path, doc = self.plan(self.pkgs(*good))
        dry = self.apply(path, doc, confirm=False)
        self.assertEqual(dry["mode"], "dry-run")
        self.assertTrue(all(self.dev.packages[p]["enabled"] for p in good))
        out = self.apply(path, doc)
        self.assertEqual(out["batches"][0]["result"]["disabled"], good)
        self.assertFalse(any(self.dev.packages[p]["enabled"] for p in good))
        cmds = [c for c in self.dev.log if c[0] == "pm" and c[1] != "list"]
        self.assertTrue(all(c[:4] == ["pm", "disable-user", "--user", "0"] for c in cmds))
        # restore: preview first, then for real; pre-existing disabled apps are left alone
        self.assertTrue(run_restore(self.adb, self.work)["dry_run"])
        self.assertFalse(self.dev.packages[good[0]]["enabled"])
        res = run_restore(self.adb, self.work, dry_run=False)
        self.assertEqual(sorted(res["switched_on"]), sorted(good))
        self.assertTrue(all(self.dev.packages[p]["enabled"] for p in good))
        self.assertFalse(self.dev.packages["com.android.nfc"]["enabled"])
        self.assertEqual(run_restore(self.adb, self.work, dry_run=False)["to_switch_on"], [])

    def test_unhealthy_phone_triggers_automatic_rollback_and_stop(self):
        names = ["com.facebook.katana", "com.facebook.appmanager", "com.facebook.system", "com.facebook.services",
                 "com.netflix.partner.activation", "com.netflix.mediaclient", "com.linkedin.android"]
        for p in names:
            self.dev.add_package(p)
        self.dev.break_on_disable = {"com.facebook.services"}
        path, doc = self.plan(self.pkgs(*names))
        out = self.apply(path, doc)
        r = out["batches"][0]["result"]
        self.assertIn("stopped", r)
        self.assertEqual(r["rolled_back"], names[:5])
        self.assertTrue(all(self.dev.packages[p]["enabled"] for p in names), "group re-enabled, later group never touched")
        self.assertEqual(run_restore(self.adb, self.work)["to_switch_on"], [])

    def test_phone_refusing_a_disable_is_reported_not_claimed(self):
        for p in ("com.facebook.katana", "com.facebook.appmanager"):
            self.dev.add_package(p)
        self.dev.refuse_disable = {"com.facebook.appmanager"}
        path, doc = self.plan(self.pkgs("com.facebook.katana", "com.facebook.appmanager"))
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual(r["disabled"], ["com.facebook.katana"])
        self.assertEqual(r["refused_by_phone"], ["com.facebook.appmanager"])
        self.assertEqual(run_restore(self.adb, self.work)["to_switch_on"], ["com.facebook.katana"])

    def test_snapshot_is_taken_before_any_change(self):
        self.dev.add_package("com.facebook.katana")
        path, doc = self.plan(self.pkgs("com.facebook.katana"))
        out = self.apply(path, doc)
        with open(out["state_backup"]) as fh:
            snap = json.load(fh)
        self.assertTrue(snap["packages"]["com.facebook.katana"]["enabled"])
        self.assertEqual(snap["defaults"]["keyboard"], "com.google.android.inputmethod.latin")
        journal = read_journal(os.path.join(self.work, "FAKE123", "journal.jsonl"))
        self.assertEqual(journal[0]["action"], "snapshot")
        first_change = next(i for i, e in enumerate(journal) if e["action"] == "disable")
        self.assertGreater(first_change, 0)
        self.assertTrue(all(e["serial"] == "FAKE123" and e["ts"] for e in journal))
        self.assertEqual(journal[first_change]["command"], ["shell", "pm", "disable-user", "--user", "0", "com.facebook.katana"])


class Storage(Base):
    def test_paths_outside_shared_storage(self):
        for p in ["/data/data/com.whatsapp/databases/msgstore.db", "/system/build.prop", ROOT + "/../../data/x",
                  ROOT + "/Download/../../../system/x", "/storage/emulated/10/x", "Download/a", ROOT + "/Android/data/com.x/files/a",
                  ROOT + "/Download/a\x00", "/sdcard/..", ROOT]:
            self.refused(self.files(p), "batch f1 file 1")

    def test_symlinks_are_refused(self):
        self.dev.add_file("/data/secret/keys.db", b"k" * 10)
        self.dev.symlinks[ROOT + "/Download/link.db"] = "/data/secret/keys.db"
        self.refused(self.files(ROOT + "/Download/link.db"), "not a plain file")
        self.dev.symlinks.clear()
        self.dev.symlinks[ROOT + "/Download/linkdir"] = "/data/secret"
        self.refused(self.files(ROOT + "/Download/linkdir/keys.db"), "points somewhere else")
        self.assertIn("/data/secret/keys.db", self.dev.files)

    def test_directories_and_wildcards_are_not_expanded(self):
        self.dev.add_file(ROOT + "/Download/a.bin")
        self.refused(self.files(ROOT + "/Download"), "not a plain file")
        path, doc = self.plan(self.files(ROOT + "/Download/*"))
        out = self.apply(path, doc)
        self.assertEqual(out["batches"][0]["skipped_count"], 1)
        self.assertIn(ROOT + "/Download/a.bin", self.dev.files)

    def test_refuses_when_phone_cannot_resolve_paths(self):
        self.dev.add_file(ROOT + "/Download/a.bin")
        self.dev.tools -= {"realpath", "readlink"}
        self.refused(self.files(ROOT + "/Download/a.bin"), "refusing to delete")

    def test_personal_areas_need_the_user_to_have_seen_a_sample(self):
        for p in ("DCIM/Camera/a.jpg", "Pictures/b.png", "WhatsApp/Media/WhatsApp Images/c.jpg"):
            self.dev.add_file(ROOT + "/" + p)
            self.refused(self.files(ROOT + "/" + p), "user_saw_sample")
        self.refused(self.files(ROOT + "/DCIM/Camera/a.jpg", user_saw_sample="yes"), "true or false")

    def test_plan_edited_after_dry_run(self):
        self.dev.add_file(ROOT + "/Download/a.bin")
        self.dev.add_file(ROOT + "/Download/b.bin")
        path, doc = self.plan(self.files(ROOT + "/Download/a.bin"))
        code = self.apply(path, doc, confirm=False)["approval_code"]
        doc["batches"][0]["files"].append({"path": ROOT + "/Download/b.bin"})
        with open(path, "w") as fh:
            json.dump(doc, fh)
        with self.assertRaises(Refused) as cm:
            run_apply(self.adb, path, workdir=self.work, confirm=code)
        self.assertIn("approval code does not match", cm.exception.message)
        self.assertEqual(len(self.dev.files), 2)

    def test_plan_edited_to_skip_backup(self):
        self.dev.add_file(ROOT + "/Download/a.bin")
        text = self.refused(self.files(ROOT + "/Download/a.bin", backup=False), "without keeping a copy")
        self.assertIn("--delete-without-backup f1", text)
        self.refused(self.files(ROOT + "/Download/a.bin", backup=False), "without keeping a copy", no_backup_batches=["other"])
        self.refused(self.files(ROOT + "/Download/a.bin", backup="false"), "true or false")
        self.refused(self.files(ROOT + "/Download/a.bin", verify=False), "unknown keys")
        path, doc = self.plan(self.files(ROOT + "/Download/a.bin", backup=False))
        out = self.apply(path, doc, no_backup_batches=["f1"])
        self.assertEqual(out["batches"][0]["result"]["deleted"], 1)
        self.assertIsNone(out["batches"][0]["result"]["backup_folder"])

    def test_hostile_names_end_to_end(self):
        for n in NASTY:
            self.dev.add_file(ROOT + "/Download/n/" + n, ("data:" + n).encode())
        keep = ROOT + "/Download/n/keep.txt"
        self.dev.add_file(keep, b"keep me")
        self.dev.add_file("/data/local/tmp/canary", b"c")
        path, doc = self.plan(self.files(*[ROOT + "/Download/n/" + n for n in NASTY]))
        out = self.apply(path, doc)
        r = out["batches"][0]["result"]
        self.assertEqual(r["deleted"], len(NASTY))
        self.assertEqual(sorted(self.dev.files), sorted([keep, "/data/local/tmp/canary"]))
        with open(os.path.join(r["backup_folder"], "manifest.jsonl")) as fh:
            manifest = [json.loads(l) for l in fh]
        self.assertEqual({m["phone_path"] for m in manifest}, {ROOT + "/Download/n/" + n for n in NASTY})
        for m in manifest:
            with open(m["local"], "rb") as fh:
                self.assertEqual(fh.read().decode(), "data:" + m["phone_path"].rsplit("/n/", 1)[1])
        # every rm named exactly one planned path
        rms = [c for c in self.dev.log if c[0] == "rm"]
        self.assertEqual(sorted(c[1] for c in rms), sorted(ROOT + "/Download/n/" + n for n in NASTY))
        self.assertTrue(all(len(c) == 2 for c in rms))

    def test_bad_copy_means_no_delete(self):
        a, b = ROOT + "/Download/a.bin", ROOT + "/Download/b.bin"
        self.dev.add_file(a, b"A" * 100)
        self.dev.add_file(b, b"B" * 100)
        self.dev.corrupt_pull = {a}
        path, doc = self.plan(self.files(a, b))
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual((r["deleted"], r["failed_count"]), (1, 1))
        self.assertIn("NOT deleted", r["failed"][0]["why"])
        self.assertIn(a, self.dev.files)
        self.assertNotIn(b, self.dev.files)
        self.assertEqual(len([f for f in os.listdir(os.path.join(r["backup_folder"], "Download"))]), 1)

    def test_no_hash_tool_uses_double_copy(self):
        a = ROOT + "/Download/a.bin"
        self.dev.add_file(a, b"A" * 100)
        self.dev.tools -= {"sha256sum", "md5sum"}
        path, doc = self.plan(self.files(a))
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(len([c for c in self.dev.raw_log if "pull" in c]), 2)

    def test_changed_or_missing_files_are_skipped_not_deleted(self):
        a, b = ROOT + "/Download/a.bin", ROOT + "/Download/b.bin"
        self.dev.add_file(a, b"new contents!")
        path, doc = self.plan([{"id": "f1", "kind": "delete_files", "title": "t",
                                "files": [{"path": a, "size": 5, "mtime": NOW - 1000}, {"path": b, "size": 1}]}])
        out = self.apply(path, doc)
        self.assertEqual(out["batches"][0]["skipped_count"], 2)
        self.assertEqual(out["batches"][0]["result"]["deleted"], 0)
        self.assertIn(a, self.dev.files)

    def test_phone_that_will_not_delete_is_reported(self):
        a = ROOT + "/Download/a.bin"
        self.dev.add_file(a, b"A")
        self.dev.undeletable = {a}
        path, doc = self.plan(self.files(a))
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual((r["deleted"], r["bytes_deleted"], r["failed_count"]), (0, 0, 1))

    def test_not_enough_disk_space(self):
        import unbloat.apply as ap
        self.dev.add_file(ROOT + "/Download/a.bin", b"A" * 10)
        old = ap.free_disk
        ap.free_disk = lambda _p: 1024
        try:
            self.refused(self.files(ROOT + "/Download/a.bin"), "Not enough free space")
        finally:
            ap.free_disk = old

    def test_trim_reports_measured_number_only(self):
        path, doc = self.plan([{"id": "c1", "kind": "trim_caches", "title": "t"}])
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual(r["freed_measured"], 5000 * 4096)
        self.dev.trim_gain_blocks = 0
        self.assertEqual(self.apply(path, doc)["batches"][0]["result"]["freed_measured"], 0)
        self.dev.tools -= {"stat", "df"}
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertIsNone(r["freed_measured"])
        self.assertIn("no claim", r["note"])
        self.assertIn(["pm", "trim-caches", "999999999999999"], self.dev.log)

    def test_dropped_connection_stops_cleanly_and_can_resume(self):
        from unbloat.apply import Interrupted
        files = [ROOT + "/Download/f%02d.bin" % i for i in range(30)]
        for f in files:
            self.dev.add_file(f, f.encode())
        path, doc = self.plan(self.files(*files))
        self.dev.calls = 0
        self.dev.offline_after = 60
        with self.assertRaises(Interrupted):
            self.apply(path, doc)
        left = len(self.dev.files)
        self.assertTrue(0 < left < 30)
        journal = read_journal(os.path.join(self.work, "FAKE123", "journal.jsonl"))
        ok = [e for e in journal if e["action"] == "delete" and e["result"] == "ok"]
        self.assertEqual(len(ok), 30 - left)
        for e in ok:
            self.assertTrue(os.path.isfile(e["backup"]), "every deleted file has its verified copy")
        self.dev.offline_after = None
        out = self.apply(path, doc)
        self.assertEqual(out["batches"][0]["result"]["deleted"], left)
        self.assertEqual(self.dev.files, {})

    def test_stale_gallery_entries_are_cleared(self):
        a = ROOT + "/DCIM/Camera/it's.jpg"
        self.dev.add_file(a, b"J" * 50)
        self.dev.media_rows = {a}
        path, doc = self.plan(self.files(a, user_saw_sample=True))
        r = self.apply(path, doc)["batches"][0]["result"]
        self.assertEqual(r["media_index"]["stale"], 0)
        self.assertEqual(r["media_index"]["cleared"], 1)
        self.assertEqual(self.dev.media_rows, set())

    def test_cli_refusal_exit_code_and_json(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        path, _ = self.plan(self.files("/data/x"))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["plan", path, "--json", "--workdir", self.work], adb=self.adb)
        doc = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"]["type"], "refused")

    def test_static_plan_rules(self):
        ok = lambda b: check_plan({"version": 1, "serial": "S", "batches": b}, "S")[1]
        self.assertTrue(ok([]))
        self.assertTrue(ok("nope"))
        self.assertTrue(ok([{"id": "a b", "kind": "trim_caches"}]))
        self.assertTrue(ok([{"id": "a", "kind": "trim_caches"}, {"id": "a", "kind": "trim_caches"}]))
        self.assertTrue(ok(self.files(ROOT + "/Download/a", ROOT + "/Download/a")))
        self.assertTrue(ok([{"id": "a", "kind": "delete_files", "files": [{"path": ROOT + "/Download/a", "glob": True}]}]))
        self.assertTrue(ok([{"id": "a", "kind": "delete_files", "files": [{"path": ROOT + "/Download/a", "size": -1}]}]))
        self.assertTrue(ok([{"id": "a", "kind": "delete_files", "files": [ROOT + "/Download/a"]}]))
        self.assertTrue(ok([{"id": "a", "kind": "delete_files", "files": [{"path": ROOT + "/x%d" % i} for i in range(5001)]}]))
        self.assertFalse(ok([{"id": "a", "kind": "trim_caches", "title": "x"}]))
        self.assertTrue(check_plan({"version": 2, "serial": "S", "batches": [{"id": "a", "kind": "trim_caches"}]}, "S")[1])


if __name__ == "__main__":
    unittest.main()
