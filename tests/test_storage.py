import json
import os
import tempfile
import unittest

from tests.fakeadb import NOW, ROOT, FakeDevice, fixture
from tests.test_core import NASTY
from unbloat.storage import analyze
from unbloat.storage.listing import parse_printf_listing
from unbloat.storage.parsers import (parse_df, parse_device_time, parse_diskstats, parse_statfs, parse_usagestats)
from unbloat.storage.scan import parse_package_list, scan_storage


class Parsers(unittest.TestCase):
    def test_diskstats_real(self):
        sizes = parse_diskstats(fixture("emu-api34", "diskstats.txt"))
        self.assertGreater(len(sizes), 100)
        self.assertEqual(set(sizes["com.google.android.youtube"]), {"app", "data", "cache"})

    def test_diskstats_missing_truncated_or_malformed(self):
        full = fixture("emu-api34", "diskstats.txt")
        self.assertIsNone(parse_diskstats(""))
        self.assertIsNone(parse_diskstats("Latency: 1ms\nData-Free: 9455724K / 10167132K total = 93% free\n"))
        self.assertIsNone(parse_diskstats(full[: len(full) // 2]))
        self.assertIsNone(parse_diskstats(full[:-40]))
        self.assertIsNone(parse_diskstats('Package Names: ["a","b"]\nApp Sizes: [1]\nApp Data Sizes: [1,2]\nCache Sizes: [1,2]\n'))
        self.assertIsNone(parse_diskstats('Package Names: ["a"]\nApp Sizes: ["x"]\nApp Data Sizes: [1]\nCache Sizes: [1]\n'))
        self.assertIsNone(parse_diskstats('Package Names: {"a":1}\nApp Sizes: [1]\nApp Data Sizes: [1]\nCache Sizes: [1]\n'))

    def test_older_android_fixtures(self):
        a8 = parse_diskstats(fixture("emu-api26", "diskstats.txt"))
        self.assertIsNone(a8["com.android.providers.telephony"]["data"])  # Android 8 has no data sizes
        self.assertGreater(a8["com.android.providers.telephony"]["app"], 0)
        a10 = parse_diskstats(fixture("emu-api29", "diskstats.txt"))
        self.assertIsInstance(next(iter(a10.values()))["data"], int)
        u8 = parse_usagestats(fixture("emu-api26", "usagestats.txt"))  # 'lastTime="1/1/1970, 5:30 AM"' style
        self.assertEqual(u8["com.android.providers.calendar"], 0.0)
        self.assertGreater(u8["com.android.providers.telephony"], 1.7e9)
        self.assertTrue(parse_usagestats(fixture("emu-api29", "usagestats.txt")))
        for v in ("26", "29"):
            self.assertEqual(parse_df(fixture("emu-api" + v, "df.txt"))["total"], 10190136 * 1024)
        from unbloat.packages.state import parse_device_admins, parse_dumpsys_role
        role = fixture("emu-api29", "role.txt")
        self.assertEqual(parse_dumpsys_role(role, "android.app.role.SMS"), ["com.android.messaging"])
        self.assertEqual(parse_dumpsys_role(role, "android.app.role.CALL_SCREENING"), [])
        self.assertEqual(parse_dumpsys_role(fixture("emu-api26", "role.txt"), "android.app.role.SMS"), [])
        for v in ("26", "29", "34"):
            self.assertEqual(parse_device_admins(fixture("emu-api" + v, "device_policy.txt")), [])

    def test_usagestats_real(self):
        usage = parse_usagestats(fixture("emu-api34", "usagestats.txt"))
        self.assertGreater(len(usage), 50)
        self.assertEqual(usage["com.google.android.youtube"], 0.0)  # epoch zero = never opened

    def test_usagestats_variants_and_garbage(self):
        old = '  package=com.a totalTime="00:10" lastTime="2026-11-01 08:00:00"\n  package=com.a totalTime="1" lastTime="2026-11-10 08:00:00"\n'
        got = parse_usagestats(old)
        self.assertEqual(got["com.a"], parse_device_time("2026-11-10 08:00:00"))
        self.assertIsNone(parse_usagestats('package=com.b x lastTimeUsed="tomorrow-ish"')["com.b"])
        self.assertEqual(parse_usagestats("package=com.c lastTimeUsed=\"2026-11"), {})
        self.assertEqual(parse_usagestats("\x00\xff nothing here"), {})

    def test_space(self):
        self.assertEqual(parse_statfs("4096 2541783 2401602 2364738\n")["total"], 4096 * 2541783)
        for bad in ("", "4096 x 1 1", "1 2 3", "0 0 0 0", "stat: not found"):
            self.assertIsNone(parse_statfs(bad))
        real = parse_df(fixture("emu-api34", "df.txt"))
        self.assertEqual(real["total"], 10167132 * 1024)
        toolbox = "Filesystem               Size     Used     Free   Blksize\n/storage/emulated       10.9G     5.2G     5.7G   4096\n"
        self.assertAlmostEqual(parse_df(toolbox)["free"] / 1024 ** 3, 5.7, places=1)
        for bad in ("", "Filesystem 1K-blocks Used\n", "garbage\nmore garbage here now\n", "Filesystem 1K-blocks Used Available\n/x a b c\n"):
            self.assertIsNone(parse_df(bad))

    def test_listing_with_hostile_names(self):
        raw = b"".join(b"%d\t17000.25\t%s\x00" % (i, (ROOT + "/d/" + n).encode()) for i, n in enumerate(NASTY))
        rows, bad = parse_printf_listing(raw + b"broken-record\x00x\ty\tz\x00")
        self.assertEqual([r["path"] for r in rows], [ROOT + "/d/" + n for n in NASTY])
        self.assertEqual(bad, 2)
        rows, _ = parse_printf_listing(b"5\t1.0\t/sdcard/\xff\xfe.bin\x00")
        self.assertEqual(rows[0]["path"].encode("utf-8", "surrogateescape"), b"/sdcard/\xff\xfe.bin")

    def test_package_list(self):
        self.assertEqual(parse_package_list("package:com.a\npackage:/x/y.apk=com.b  installer=null uid:1\njunk\npackage:\n"), ["com.a", "com.b"])


class Analysis(unittest.TestCase):
    def rows(self, spec):
        return [{"path": ROOT + "/" + p, "size": s, "mtime": NOW - d * 86400} for p, s, d in spec]

    def test_messaging_types(self):
        c = lambda p: analyze.classify_messaging(ROOT + "/" + p)
        self.assertEqual(c("WhatsApp/Media/WhatsApp Images/IMG.jpg"), ("WhatsApp", "received images"))
        self.assertEqual(c("WhatsApp/Media/WhatsApp Images/Sent/IMG.jpg"), ("WhatsApp", "sent images"))
        self.assertEqual(c("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video/Sent/V.mp4"), ("WhatsApp", "sent video"))
        self.assertEqual(c("Android/media/com.whatsapp/WhatsApp/Media/.Statuses/s.jpg"), ("WhatsApp", "status cache"))
        self.assertEqual(c("WhatsApp/Media/WhatsApp Voice Notes/202601/PTT.opus"), ("WhatsApp", "voice notes"))
        self.assertEqual(c("WhatsApp/Media/WhatsApp Documents/a.pdf"), ("WhatsApp", "documents"))
        self.assertEqual(c("WhatsApp Business/Media/WhatsApp Business Images/a.jpg"), ("WhatsApp Business", "received images"))
        self.assertEqual(c("Telegram/Telegram Video/a.mp4"), ("Telegram", "video"))
        self.assertEqual(c("Pictures/Telegram/a.jpg"), ("Telegram", "images"))
        self.assertIsNone(c("DCIM/Camera/a.jpg"))
        self.assertIsNone(c("WhatsAppClone/Media/a.jpg"))

    def test_tags(self):
        t = lambda p: analyze.tags_for(ROOT + "/" + p)
        self.assertEqual(t("Download/app.APK"), ["installers", "downloads"])
        self.assertIn("screenshots", t("Pictures/Screenshots/Screenshot_1.png"))
        self.assertIn("screenshots", t("DCIM/Screenshots/a.jpg"))
        self.assertIn("screen_recordings", t("Movies/Screen recordings/a.mp4"))
        self.assertIn("screen_recordings", t("DCIM/ScreenRecorder/Screenrecorder-1.mp4"))
        self.assertEqual(t("Documents/notes.txt"), [])

    def test_tree_largest_downloads_empty(self):
        rows = self.rows([("Download/a.zip", 500, 400), ("Download/b.apk", 100, 5), ("DCIM/Camera/x.jpg", 900, 40), ("top.bin", 7, 1)])
        tree = analyze.tree(rows)
        self.assertEqual(tree[0]["size"], 1507)
        self.assertEqual([t["folder"] for t in tree[1:]], ["DCIM", "Download"])
        self.assertEqual(tree[1]["subfolders"][0]["folder"], "DCIM/Camera")
        self.assertEqual(analyze.largest(rows, 2)[0]["path"], ROOT + "/DCIM/Camera/x.jpg")
        dl = analyze.downloads(rows, NOW)
        self.assertEqual(dl["count"], 2)
        self.assertEqual({b["age"]: b["count"] for b in dl["by_age"]}, {"under 30 days": 1, "1 to 6 months": 0, "6 to 12 months": 0, "over a year": 1})
        dirs = [ROOT + d for d in ("/Download", "/Download/old", "/Download/old/deep", "/DCIM", "/DCIM/Camera", "/Android/data/x", "/Music")]
        self.assertEqual(analyze.empty_dirs(dirs, rows), [ROOT + "/Download/old/deep"])

    def test_duplicates_keep_camera_copy(self):
        rows = self.rows([("WhatsApp/Media/WhatsApp Images/a.jpg", 10, 3), ("DCIM/Camera/a.jpg", 10, 1), ("Download/a.jpg", 10, 9), ("Download/other", 10, 9)])
        groups = analyze.same_size_groups(rows, 1)
        hashes = {r["path"]: "h1" for r in rows[:3]}
        hashes[rows[3]["path"]] = "h2"
        dups = analyze.duplicate_groups(groups, hashes)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["keep"], ROOT + "/DCIM/Camera/a.jpg")
        self.assertEqual(dups[0]["wasted"], 20)
        self.assertEqual(analyze.duplicate_groups(groups, {}), [])
        self.assertEqual(analyze.same_size_groups(self.rows([("a", 0, 1), ("b", 0, 1)]), 0), [])


class Scan(unittest.TestCase):
    def device(self):
        dev = FakeDevice()
        big = b"V" * 200000
        dev.add_file(ROOT + "/DCIM/Camera/VID_1.mp4", big)
        dev.add_file(ROOT + "/WhatsApp/Media/WhatsApp Video/Sent/VID-1.mp4", big)
        dev.add_file(ROOT + "/Download/copy of video.mp4", big)
        dev.add_file(ROOT + "/Download/same-size-different.bin", b"W" * 200000)
        dev.add_file(ROOT + "/Download/setup.apk", b"A" * 5000, NOW - 500 * 86400)
        dev.add_file(ROOT + "/Pictures/Screenshots/Screenshot_1.png", b"S" * 3000)
        dev.add_file(ROOT + "/Android/data/com.x/cache/blob", b"C" * 100)
        for n in NASTY:
            dev.add_file(ROOT + "/Download/nasty/" + n, n.encode())
        dev.dirs.add(ROOT + "/Download/empty/inner")
        dev.dirs.add(ROOT + "/Download/empty")
        dev.add_package("com.user.game", system=False)
        return dev

    def scan(self, dev, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            data = scan_storage(dev.adb(), workdir=tmp, **kw)
            with open(data["saved"]["index"]) as fh:
                index = json.load(fh)
            json.dumps(data)
            return data, index

    def test_full_scan(self):
        dev = self.device()
        data, index = self.scan(dev)
        self.assertEqual(data["file_count"], 7 + len(NASTY))
        self.assertEqual({f[0] for f in index["files"]} >= {ROOT + "/Download/nasty/" + n for n in NASTY}, True)
        groups = {g["group"]: g for g in data["groups"]}
        self.assertEqual(groups["installers"]["count"], 1)
        self.assertEqual(groups["duplicates"]["count"], 2)
        self.assertEqual(groups["messaging:WhatsApp:sent video"]["size"], 200000)
        self.assertEqual(data["duplicates"]["groups"][0]["keep"], ROOT + "/DCIM/Camera/VID_1.mp4")
        self.assertEqual(data["duplicates"]["method"], "sha256sum")
        self.assertEqual(data["empty_folders"]["sample"], [ROOT + "/Download/empty/inner"])
        self.assertTrue(data["apps"]["available"])
        self.assertTrue(data["android_data"]["visible"])
        self.assertEqual(data["space"]["total"], 4096 * 2541783)
        # only the same-size files were hashed, not all of them
        hashed = [c for c in dev.log if c[0] == "sha256sum"]
        self.assertEqual(sum(len(c) - 1 for c in hashed), 4)
        # read-only: nothing that changes the phone except Android's own size job
        for c in dev.log:
            self.assertNotIn(c[0], ("rm", "mv", "content"))
            self.assertFalse(c[0] == "pm" and c[1] != "list")

    def test_old_phone_without_printf_or_sha256(self):
        dev = self.device()
        dev.find_printf = False
        dev.tools.discard("sha256sum")
        data, _ = self.scan(dev)
        self.assertEqual(data["listing_method"], "find-print0+stat")
        self.assertEqual(data["duplicates"]["method"], "md5sum")
        self.assertEqual(len(data["duplicates"]["groups"][0]["extra"]), 2)
        self.assertEqual(data["file_count"], 7 + len(NASTY))

    def test_phone_with_no_hash_tool_pulls_only_collisions(self):
        dev = self.device()
        dev.tools -= {"sha256sum", "md5sum"}
        data, _ = self.scan(dev)
        self.assertEqual(data["duplicates"]["method"], "pull+sha256")
        self.assertEqual(len(data["duplicates"]["groups"][0]["extra"]), 2)
        self.assertEqual(len([c for c in dev.raw_log if "pull" in c]), 4)

    def test_degrades_when_dumpsys_is_useless(self):
        dev = self.device()
        dev.outputs = {"diskstats": "Latency: 1ms\n", "usagestats": "nothing useful"}
        dev.tools.discard("find")
        import unbloat.storage.scan as s
        old = s.time.sleep
        s.time.sleep = lambda _x: None
        try:
            data, _ = self.scan(dev, duplicates=False)
        finally:
            s.time.sleep = old
        self.assertFalse(data["apps"]["available"])
        self.assertEqual(data["file_count"], 0)
        notes = " ".join(data["notes"])
        self.assertIn("did not report per-app sizes", notes)
        self.assertIn("no 'find' tool", notes)

    def test_unused_apps(self):
        dev = self.device()
        dev.outputs["diskstats"] = 'Package Names: ["com.user.game","com.android.settings"]\nApp Sizes: [900,5]\nApp Data Sizes: [100,5]\nCache Sizes: [10,1]\n'
        dev.outputs["usagestats"] = '  package=com.user.game totalTimeUsed="00:00" lastTimeUsed="2026-05-01 10:00:00"\n  package=com.android.settings lastTimeUsed="2026-11-17 09:00:00"\n'
        data, _ = self.scan(dev)
        items = data["unused_apps"]["items"]
        self.assertEqual([a["package"] for a in items], ["com.user.game"])
        self.assertEqual(items[0]["days_since_opened"], 200)


if __name__ == "__main__":
    unittest.main()
