"""`photos`: opt-in help for reviewing photos.

A local pre-pass finds exact duplicates (on the phone, by hash), then, when
Pillow is installed, near-duplicates and probably-blurry shots from small
copies. Only those candidates are laid out on contact sheets, so an
assistant with vision can look at many photos cheaply.

Nothing here deletes anything. Contact sheets are only built after the user
has agreed that images will be sent to their assistant's AI provider.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

from .adb import Adb, AdbError
from .device import device_info, probe_tools
from .paths import ROOT, PathRejected, normalize_remote
from .remote import hash_many, pick_hash_tool
from .storage import analyze
from .storage.listing import list_storage
from .util import human, show
from .workspace import Workspace, stamp

PILLOW_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
THUMB = 256
NEAR_DISTANCE = 6
NEAR_WINDOW = 25
BLUR_BELOW = 35.0
SHEET_COLS, SHEET_ROWS = 5, 4


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def dhash(img: Any) -> int:
    """64-bit difference hash: similar pictures give similar bits."""
    from PIL import Image

    bilinear = getattr(Image, "Resampling", Image).BILINEAR
    px = img.convert("L").resize((9, 8), bilinear).tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def blur_score(img: Any) -> float:
    """Spread of edge strength. Low numbers mean few sharp edges (blurry,
    or simply a plain subject, which is why a person still has to look)."""
    from PIL import ImageFilter, ImageStat

    gray = img.convert("L")
    edges = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    if edges.width > 4 and edges.height > 4:
        edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))  # the filter leaves the border untouched
    return float(ImageStat.Stat(edges).var[0])


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def near_groups(items: List[Dict[str, Any]]) -> List[List[int]]:
    """Group pictures taken close together that look almost the same."""
    order = sorted(range(len(items)), key=lambda i: (items[i]["mtime"] or 0, items[i]["path"]))
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pos, i in enumerate(order):
        for j in order[pos + 1: pos + 1 + NEAR_WINDOW]:
            if hamming(items[i]["dhash"], items[j]["dhash"]) <= NEAR_DISTANCE:
                parent[find(i)] = find(j)
    groups: Dict[int, List[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g, key=lambda i: order.index(i)) for g in groups.values() if len(g) > 1]


def build_sheets(entries: List[Dict[str, Any]], out_dir: str) -> List[str]:
    """Lay thumbnails out in numbered grids. `entries` need 'thumb' and 'n'."""
    from PIL import Image, ImageDraw

    per = SHEET_COLS * SHEET_ROWS
    cell = THUMB + 8
    paths = []
    for s in range(0, len(entries), per):
        chunk = entries[s:s + per]
        rows = (len(chunk) + SHEET_COLS - 1) // SHEET_COLS
        sheet = Image.new("RGB", (SHEET_COLS * cell, rows * (cell + 16)), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for k, e in enumerate(chunk):
            x = (k % SHEET_COLS) * cell + 4
            y = (k // SHEET_COLS) * (cell + 16)
            with Image.open(e["thumb"]) as im:
                sheet.paste(im, (x + (THUMB - im.width) // 2, y + 16 + (THUMB - im.height) // 2))
            draw.text((x + 2, y + 2), "#%d" % e["n"], fill=(255, 255, 255))
        path = os.path.join(out_dir, "sheet-%03d.jpg" % (len(paths) + 1))
        sheet.save(path, "JPEG", quality=80)
        paths.append(path)
    return paths


def run_photos(
    adb: Adb,
    workdir: Optional[str] = None,
    folders: Optional[List[str]] = None,
    limit: int = 2000,
    consent: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    say = progress or (lambda _m: None)
    info = device_info(adb)
    tools = probe_tools(adb)
    roots = []
    for f in folders or [ROOT + "/DCIM", ROOT + "/Pictures"]:
        try:
            roots.append(normalize_remote(f))
        except PathRejected as exc:
            raise AdbError("Cannot review %s: %s." % (show(f), exc), "Give a folder inside the phone's shared storage.")
    files: List[Dict[str, Any]] = []
    for root in roots:
        files.extend(list_storage(adb, tools, root=root)["files"])  # type: ignore[arg-type]
    images = [f for f in files if analyze.kind_of(str(f["path"])) == "images" and isinstance(f.get("size"), int)]
    images.sort(key=lambda f: -(f["mtime"] or 0))
    total_size = sum(f["size"] for f in images)
    notes: List[str] = []
    data: Dict[str, Any] = {
        "folders": roots,
        "images_found": len(images),
        "images_size": total_size,
        "pillow": have_pillow(),
        "exact_duplicates": [],
        "near_duplicates": [],
        "possibly_blurry": [],
        "contact_sheets": [],
        "notes": notes,
        "reminder": "Nothing is deleted here. To remove photos, put exact paths in a plan; DCIM and Pictures "
        "need the user to have seen what is going. If the user backs up to Google Photos, its own "
        "'Free up space' is the right tool for already-backed-up originals.",
    }
    # 1. exact duplicates, compared on the phone
    groups = analyze.same_size_groups(images, 1)
    tool = pick_hash_tool(tools)
    if groups and tool:
        cands = [str(r["path"]) for g in groups for r in g]
        say("Comparing %d photos that share a size..." % len(cands))
        hashes = hash_many(adb, tool, cands, {str(r["path"]): int(r["size"]) for g in groups for r in g})
        data["exact_duplicates"] = analyze.duplicate_groups(groups, hashes)
    elif groups:
        notes.append("This phone cannot compare file contents itself, so exact duplicates were not checked here.")
    if not data["pillow"]:
        notes.append(
            "Pillow is not installed, so near-duplicate and blur checks and contact sheets are unavailable. "
            "Install it with: python -m pip install Pillow"
        )
        return data
    from PIL import Image, ImageOps

    todo = [f for f in images if analyze.ext_of(str(f["path"])) in PILLOW_EXT][:limit]
    skipped_format = sum(1 for f in images if analyze.ext_of(str(f["path"])) not in PILLOW_EXT)
    if skipped_format:
        notes.append("%d images are in a format (such as HEIC or RAW) that could not be opened here and were skipped." % skipped_format)
    if len(images) - skipped_format > limit:
        notes.append("Only the newest %d photos were looked at (raise --limit to see more)." % limit)
    ws = Workspace(workdir, str(info["serial"]))
    out_dir = ws.dir("photos", stamp())
    thumbs = os.path.join(out_dir, "thumbs")
    os.makedirs(thumbs, exist_ok=True)
    items: List[Dict[str, Any]] = []
    failed = 0
    with tempfile.TemporaryDirectory(prefix="unbloat-photo-") as tmp:
        target = os.path.join(tmp, "p")
        for n, f in enumerate(todo):
            try:
                res = adb.pull(str(f["path"]), target, timeout=120 + f["size"] / (300 * 1024))
                if res.code != 0:
                    failed += 1
                    continue
                with Image.open(target) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((THUMB, THUMB))
                    tpath = os.path.join(thumbs, "%05d.jpg" % len(items))
                    im.save(tpath, "JPEG", quality=70)
                    items.append({"n": len(items), "path": f["path"], "size": f["size"], "mtime": f["mtime"],
                                  "dhash": dhash(im), "blur": round(blur_score(im), 1), "thumb": tpath})
            except AdbError:
                raise
            except Exception:
                failed += 1
            finally:
                if os.path.exists(target):
                    os.remove(target)
            if (n + 1) % 50 == 0:
                say("Looked at %d of %d photos..." % (n + 1, len(todo)))
    if failed:
        notes.append("%d photos could not be copied or opened and were skipped." % failed)
    pub = lambda i: {"n": items[i]["n"], "path": items[i]["path"], "size": items[i]["size"], "blur": items[i]["blur"]}
    near = near_groups(items)
    data["near_duplicates"] = [[pub(i) for i in g] for g in near]
    in_near = {i for g in near for i in g}
    blurry = sorted((i for i in range(len(items)) if items[i]["blur"] < BLUR_BELOW), key=lambda i: items[i]["blur"])
    data["possibly_blurry"] = [pub(i) for i in blurry]
    data["images_examined"] = len(items)
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump([{k: v for k, v in it.items() if k != "thumb"} for it in items], fh, ensure_ascii=True)
    data["index"] = os.path.join(out_dir, "index.json")
    candidates = [items[i] for g in near for i in g] + [items[i] for i in blurry if i not in in_near]
    data["candidates"] = len(candidates)
    if not consent:
        notes.append(
            "Contact sheets were NOT built. They exist so an AI assistant can look at the photos, which sends "
            "them to the company behind that assistant. If the user agrees to that, run again with "
            "--i-understand-images-go-to-my-ai-provider"
        )
    elif candidates:
        data["contact_sheets"] = build_sheets(candidates, out_dir)
        data["sheet_note"] = "Each picture is labelled #n. Look n up in near_duplicates / possibly_blurry to get its path."
    return data


def format_photos(d: Dict[str, Any]) -> str:
    lines = ["%d images, %s, in %s" % (d["images_found"], human(d["images_size"]), ", ".join(show(f) for f in d["folders"]))]
    wasted = sum(int(g["wasted"]) for g in d["exact_duplicates"])
    lines.append("Exact duplicate groups: %d (%s could be freed)" % (len(d["exact_duplicates"]), human(wasted)))
    if d["pillow"]:
        lines.append("Looked closely at %d photos" % d.get("images_examined", 0))
        lines.append("Groups of near-identical shots: %d" % len(d["near_duplicates"]))
        lines.append("Possibly blurry: %d (a person or assistant must look; plain subjects score low too)" % len(d["possibly_blurry"]))
        for s in d["contact_sheets"]:
            lines.append("Contact sheet: %s" % s)
    for n in d["notes"]:
        lines.append("Note: " + n)
    lines.append(d["reminder"])
    return "\n".join(lines)
