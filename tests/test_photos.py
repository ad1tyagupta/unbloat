import io
import os
import tempfile
import unittest

from tests.fakeadb import NOW, ROOT, FakeDevice
from unbloat import photos
from unbloat.adb import AdbError


def _jpeg(kind, seed=0, size=(320, 240)):
    from PIL import Image, ImageDraw, ImageFilter
    import random
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (200, 180, 150))
    d = ImageDraw.Draw(im)
    if kind != "flat":
        for _ in range(60):
            x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
            d.rectangle([x, y, x + rnd.randrange(8, 60), y + rnd.randrange(8, 60)],
                        fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    if kind == "blurry":
        im = im.filter(ImageFilter.GaussianBlur(9))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92 if kind != "recompressed" else 60)
    return buf.getvalue()


class NoPillowNeeded(unittest.TestCase):
    def test_exact_duplicates_without_pillow(self):
        dev = FakeDevice()
        dev.add_file(ROOT + "/DCIM/Camera/a.jpg", b"same-bytes" * 100)
        dev.add_file(ROOT + "/Pictures/copy of a.jpg", b"same-bytes" * 100)
        dev.add_file(ROOT + "/DCIM/Camera/b.jpg", b"other-bytes" * 100)
        dev.add_file(ROOT + "/Download/not-a-photo-folder.jpg", b"same-bytes" * 100)
        old = photos.have_pillow
        photos.have_pillow = lambda: False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d = photos.run_photos(dev.adb(), workdir=tmp)
        finally:
            photos.have_pillow = old
        self.assertEqual(d["images_found"], 3)
        self.assertEqual(len(d["exact_duplicates"]), 1)
        self.assertEqual(d["exact_duplicates"][0]["keep"], ROOT + "/DCIM/Camera/a.jpg")
        self.assertIn("Pillow is not installed", " ".join(d["notes"]))
        self.assertEqual(d["contact_sheets"], [])
        self.assertFalse([c for c in dev.raw_log if "pull" in c], "nothing is copied without Pillow")
        self.assertFalse([c for c in dev.log if c[0] in ("rm", "pm") and c[1:2] != ["list"]])

    def test_folders_must_be_in_shared_storage(self):
        with self.assertRaises(AdbError):
            photos.run_photos(FakeDevice().adb(), folders=["/data/data"])


@unittest.skipUnless(photos.have_pillow(), "Pillow not installed")
class WithPillow(unittest.TestCase):
    def device(self):
        dev = FakeDevice()
        base = _jpeg("sharp", 1)
        dev.add_file(ROOT + "/DCIM/Camera/IMG_1.jpg", base, NOW - 100)
        dev.add_file(ROOT + "/DCIM/Camera/IMG_1_burst.jpg", _jpeg("recompressed", 1), NOW - 99)
        dev.add_file(ROOT + "/DCIM/Camera/IMG_2.jpg", _jpeg("sharp", 2), NOW - 90)
        dev.add_file(ROOT + "/DCIM/Camera/IMG_3_blur.jpg", _jpeg("blurry", 3), NOW - 80)
        dev.add_file(ROOT + "/Pictures/IMG_1 copy.jpg", base, NOW - 50)
        dev.add_file(ROOT + "/DCIM/Camera/broken.jpg", b"not really a jpeg", NOW - 10)
        dev.add_file(ROOT + "/DCIM/Camera/IMG_9.heic", b"heic-bytes", NOW - 5)
        return dev

    def test_prepass_and_consent_gate(self):
        dev = self.device()
        with tempfile.TemporaryDirectory() as tmp:
            d = photos.run_photos(dev.adb(), workdir=tmp)
            self.assertEqual(d["images_found"], 7)
            self.assertEqual(d["images_examined"], 5)
            self.assertEqual(len(d["exact_duplicates"]), 1)
            near = [sorted(os.path.basename(e["path"]) for e in g) for g in d["near_duplicates"]]
            self.assertEqual(near, [["IMG_1 copy.jpg", "IMG_1.jpg", "IMG_1_burst.jpg"]])
            self.assertEqual([os.path.basename(e["path"]) for e in d["possibly_blurry"]], ["IMG_3_blur.jpg"])
            self.assertEqual(d["contact_sheets"], [])
            notes = " ".join(d["notes"])
            self.assertIn("Contact sheets were NOT built", notes)
            self.assertIn("HEIC", notes)
            self.assertIn("could not be copied or opened", notes)
            self.assertEqual(len(dev.files), 7, "photo review never deletes")

    def test_contact_sheets_after_consent(self):
        from PIL import Image
        dev = self.device()
        with tempfile.TemporaryDirectory() as tmp:
            d = photos.run_photos(dev.adb(), workdir=tmp, consent=True)
            self.assertEqual(len(d["contact_sheets"]), 1)
            with Image.open(d["contact_sheets"][0]) as sheet:
                self.assertEqual(sheet.size[0], 5 * 264)
            self.assertEqual(d["candidates"], 4)
            # full-size copies are not kept on the computer, only small thumbnails
            for base, _dirs, files in os.walk(tmp):
                for f in files:
                    if f.endswith(".jpg") and "sheet" not in f:
                        with Image.open(os.path.join(base, f)) as im:
                            self.assertLessEqual(max(im.size), 256)

    def test_limit(self):
        dev = self.device()
        with tempfile.TemporaryDirectory() as tmp:
            d = photos.run_photos(dev.adb(), workdir=tmp, limit=2)
            self.assertLessEqual(d["images_examined"], 2)
            self.assertIn("Only the newest 2", " ".join(d["notes"]))


if __name__ == "__main__":
    unittest.main()
