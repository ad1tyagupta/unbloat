"""End-to-end test against a real Android emulator.

Skipped unless UNBLOAT_E2E_SERIAL names a connected emulator. It pushes junk
files, then scans, plans, applies, and checks they are gone and the backup
matches; then switches a harmless app off and restores it.

Only point this at an emulator: it deletes files and switches apps off.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest

from unbloat.adb import Adb, find_adb
from unbloat.apply import run_apply
from unbloat.pick import run_pick
from unbloat.restore import run_restore
from unbloat.storage.scan import scan_storage

SERIAL = os.environ.get("UNBLOAT_E2E_SERIAL")
NAMES = ["plain.apk", "with space.bin", "quote'single.bin", "semi;colon.bin", "dollar$(reboot).bin",
         "back`tick`.bin", "uni_日本語_😀.bin", "-dash.bin", "star*glob?.bin", "new\nline.bin"]


@unittest.skipUnless(SERIAL, "set UNBLOAT_E2E_SERIAL to an emulator serial to run")
class EmulatorEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adb_path = find_adb()
        self.adb = Adb(adb_path=self.adb_path, serial=SERIAL)
        self.assertTrue(SERIAL.startswith("emulator-"), "refusing to run against a real phone")
        self.work = os.path.join(self.tmp.name, "work")
        # Shared storage mounts a little after boot completes; wait for it.
        for _ in range(60):
            if self.raw("shell", "touch /sdcard/.unbloat-ready && rm /sdcard/.unbloat-ready").returncode == 0:
                break
            time.sleep(2)

    def raw(self, *args):
        return subprocess.run([self.adb_path, "-s", SERIAL] + list(args), capture_output=True)

    def approve(self, plan_path):
        code = run_apply(self.adb, plan_path, workdir=self.work)["approval_code"]
        return run_apply(self.adb, plan_path, workdir=self.work, confirm=code)

    def test_delete_with_backup(self):
        folder = "/sdcard/Download/unbloat-e2e"
        self.raw("shell", "rm -rf " + folder + " && mkdir -p " + folder)
        local = os.path.join(self.tmp.name, "src")
        os.makedirs(local)
        digests = {}
        for i, name in enumerate(NAMES):
            data = os.urandom(4096 + i)
            src = os.path.join(local, "f%d" % i)
            with open(src, "wb") as fh:
                fh.write(data)
            res = self.raw("push", src, folder + "/" + name)
            if res.returncode != 0:
                continue  # some file systems refuse some names; skip those
            digests["/storage/emulated/0/Download/unbloat-e2e/" + name] = hashlib.sha256(data).hexdigest()
        # Android 11+ refuses names with * ? or newlines in shared storage; older versions accept them.
        self.assertGreaterEqual(len(digests), 8)
        scan_storage(self.adb, workdir=self.work, duplicates=False)
        plan = os.path.join(self.tmp.name, "plan.json")
        run_pick(self.adb, "downloads", plan, workdir=self.work)
        with open(plan) as fh:
            doc = json.load(fh)
        for b in doc["batches"]:
            b["files"] = [f for f in b["files"] if f["path"] in digests]
        with open(plan, "w") as fh:
            json.dump(doc, fh)
        out = self.approve(plan)
        r = out["batches"][0]["result"]
        self.assertEqual(r["deleted"], len(digests))
        left = self.raw("shell", "find " + folder + " -type f").stdout.strip()
        self.assertEqual(left, b"")
        with open(os.path.join(r["backup_folder"], "manifest.jsonl")) as fh:
            for line in fh:
                m = json.loads(line)
                with open(m["local"], "rb") as f:
                    self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digests[m["phone_path"]])

    def test_disable_and_restore(self):
        pkg = "com.android.egg"
        listed = self.raw("shell", "pm list packages --user 0").stdout.decode()
        if "package:%s\n" % pkg not in listed + "\n":
            self.skipTest("%s is not on this emulator" % pkg)
        self.raw("shell", "pm enable --user 0 " + pkg)
        plan = os.path.join(self.tmp.name, "pkg.json")
        with open(plan, "w") as fh:
            json.dump({"version": 1, "serial": SERIAL, "batches": [
                {"id": "egg", "kind": "disable_packages", "title": "egg", "packages": [pkg]}]}, fh)
        out = self.approve(plan)
        self.assertEqual(out["batches"][0]["result"]["disabled"], [pkg])
        self.assertIn(pkg, self.raw("shell", "pm list packages -d --user 0").stdout.decode())
        res = run_restore(self.adb, workdir=self.work, dry_run=False)
        self.assertEqual(res["switched_on"], [pkg])
        self.assertNotIn(pkg, self.raw("shell", "pm list packages -d --user 0").stdout.decode())


if __name__ == "__main__":
    unittest.main()
