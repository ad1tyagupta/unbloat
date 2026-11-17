"""Turn a file listing into findings. Pure functions, no phone needed."""
from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from ..paths import ROOT

FileRow = Dict[str, object]

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "heic", "heif", "gif", "bmp", "dng", "raw", "avif"}
VIDEO_EXT = {"mp4", "mkv", "mov", "3gp", "webm", "avi", "m4v", "ts"}
AUDIO_EXT = {"mp3", "m4a", "ogg", "opus", "wav", "aac", "flac", "amr", "wma"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "zip", "rar", "7z", "epub"}
INSTALLER_EXT = {"apk", "apks", "xapk", "apkm", "aab", "exe", "msi", "dmg"}

DAY = 86400


def ext_of(path: str) -> str:
    name = posixpath.basename(path)
    if "." not in name.strip("."):
        return ""
    return name.rsplit(".", 1)[1].lower()


def kind_of(path: str) -> str:
    e = ext_of(path)
    if e in IMAGE_EXT:
        return "images"
    if e in VIDEO_EXT:
        return "video"
    if e in AUDIO_EXT:
        return "audio"
    if e in DOC_EXT:
        return "documents"
    return "other"


def rel(path: str) -> str:
    return path[len(ROOT) + 1:] if path.startswith(ROOT + "/") else path


def _size(row: FileRow) -> int:
    s = row.get("size")
    return int(s) if isinstance(s, int) else 0


# -- messaging apps -------------------------------------------------------

_WHATSAPP_TYPES = (
    (".statuses", "status cache"),
    ("whatsapp images", "received images"),
    ("whatsapp business images", "received images"),
    ("whatsapp video", "received video"),
    ("whatsapp business video", "received video"),
    ("whatsapp documents", "documents"),
    ("whatsapp business documents", "documents"),
    ("whatsapp voice notes", "voice notes"),
    ("whatsapp business voice notes", "voice notes"),
    ("whatsapp audio", "audio"),
    ("whatsapp business audio", "audio"),
    ("whatsapp animated gifs", "gifs"),
    ("whatsapp business animated gifs", "gifs"),
    ("whatsapp stickers", "stickers"),
    ("whatsapp business stickers", "stickers"),
)

_SENT_SUFFIX = {
    "received images": "sent images",
    "received video": "sent video",
    "documents": "sent documents",
    "audio": "sent audio",
    "gifs": "sent gifs",
}

# (app name, lower-case path prefixes below the storage root, style)
MESSAGING_APPS: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("WhatsApp", ("whatsapp/media/", "android/media/com.whatsapp/whatsapp/media/"), "whatsapp"),
    (
        "WhatsApp Business",
        ("whatsapp business/media/", "android/media/com.whatsapp.w4b/whatsapp business/media/"),
        "whatsapp",
    ),
    (
        "Telegram",
        (
            "telegram/",
            "android/media/org.telegram.messenger/",
            "android/media/org.thunderdog.challegram/",
            "pictures/telegram/",
            "movies/telegram/",
        ),
        "generic",
    ),
    ("Signal", ("signal/", "pictures/signal/"), "generic"),
    ("Viber", ("viber/", "android/media/com.viber.voip/", "pictures/viber/"), "generic"),
    ("Messenger", ("pictures/messenger/", "movies/messenger/"), "generic"),
    ("Instagram", ("pictures/instagram/", "movies/instagram/"), "generic"),
    ("LINE", ("line/", "pictures/line/"), "generic"),
    ("Snapchat", ("snapchat/", "pictures/snapchat/"), "generic"),
    ("WeChat", ("tencent/micromsg/", "pictures/wechat/"), "generic"),
    ("KakaoTalk", ("kakaotalk/", "pictures/kakaotalk/"), "generic"),
    ("Discord", ("pictures/discord/",), "generic"),
)


def classify_messaging(path: str) -> Optional[Tuple[str, str]]:
    """(app, media type) for a file kept by a messaging app, else None."""
    low = rel(path).casefold()
    for app, prefixes, style in MESSAGING_APPS:
        for prefix in prefixes:
            if not low.startswith(prefix):
                continue
            rest = low[len(prefix):]
            if style == "whatsapp":
                first = rest.split("/", 1)[0]
                mtype = None
                for marker, label in _WHATSAPP_TYPES:
                    if first == marker:
                        mtype = label
                        break
                if mtype is None:
                    mtype = "other"
                if "/sent/" in "/" + rest and mtype in _SENT_SUFFIX:
                    mtype = _SENT_SUFFIX[mtype]
                return app, mtype
            return app, kind_of(path)
    return None


# -- tagging --------------------------------------------------------------

def tags_for(path: str) -> List[str]:
    """Group ids a file belongs to. Used by `pick` to build exact-path batches."""
    tags: List[str] = []
    low = rel(path).casefold()
    name = posixpath.basename(low)
    if ext_of(path) in INSTALLER_EXT:
        tags.append("installers")
    if low.startswith("download/") or low.startswith("downloads/"):
        tags.append("downloads")
    msg = classify_messaging(path)
    if msg:
        tags.append("messaging:%s:%s" % msg)
    kind = kind_of(path)
    if kind == "images" and ("/screenshots/" in "/" + low or name.startswith("screenshot")):
        tags.append("screenshots")
    if kind == "video" and (
        "/screen recordings/" in "/" + low
        or "/screenrecorder/" in "/" + low
        or "/screenrecords/" in "/" + low
        or name.startswith(("screen_recording", "screenrecord", "screen-", "screenrecorder", "screen recording"))
    ):
        tags.append("screen_recordings")
    return tags


# -- findings -------------------------------------------------------------

def _entry(row: FileRow) -> Dict[str, object]:
    return {"path": row["path"], "size": row.get("size"), "mtime": row.get("mtime")}


def tree(files: Iterable[FileRow], depth: int = 2, top: int = 12) -> List[Dict[str, object]]:
    """Sizes by top-level folder, each with its largest sub-folders."""
    sizes: Dict[Tuple[str, ...], List[int]] = defaultdict(lambda: [0, 0])
    for row in files:
        parts = rel(str(row["path"])).split("/")[:-1]
        size = _size(row)
        for d in range(0, min(depth, len(parts)) + 1):
            key = tuple(parts[:d])
            sizes[key][0] += size
            sizes[key][1] += 1
    out: List[Dict[str, object]] = []
    firsts = sorted((k for k in sizes if len(k) == 1), key=lambda k: -sizes[k][0])
    for k in firsts:
        kids = sorted((c for c in sizes if len(c) == 2 and c[0] == k[0]), key=lambda c: -sizes[c][0])[:top]
        out.append(
            {
                "folder": k[0],
                "size": sizes[k][0],
                "files": sizes[k][1],
                "subfolders": [{"folder": "/".join(c), "size": sizes[c][0], "files": sizes[c][1]} for c in kids],
            }
        )
    loose = sizes.get(tuple())
    total = loose[0] if loose else 0
    return [{"folder": "(everything)", "size": total, "files": loose[1] if loose else 0, "subfolders": []}] + out


def largest(files: Iterable[FileRow], n: int = 50) -> List[Dict[str, object]]:
    rows = sorted(files, key=lambda r: -_size(r))[:n]
    return [_entry(r) for r in rows if _size(r) > 0]


def _summary(rows: List[FileRow], now: float, sample: int = 5) -> Dict[str, object]:
    rows = sorted(rows, key=lambda r: -_size(r))
    mtimes = [int(r["mtime"]) for r in rows if isinstance(r.get("mtime"), int)]
    return {
        "count": len(rows),
        "size": sum(_size(r) for r in rows),
        "oldest_days": int((now - min(mtimes)) // DAY) if mtimes else None,
        "newest_days": int((now - max(mtimes)) // DAY) if mtimes else None,
        "sample": [_entry(r) for r in rows[:sample]],
    }


def downloads(files: Iterable[FileRow], now: float) -> Dict[str, object]:
    rows = [r for r in files if "downloads" in tags_for(str(r["path"]))]
    buckets = (("under 30 days", 0, 30), ("1 to 6 months", 30, 180), ("6 to 12 months", 180, 365), ("over a year", 365, 10 ** 6))
    by_age = []
    for label, lo, hi in buckets:
        sel = [
            r for r in rows
            if isinstance(r.get("mtime"), int) and lo <= (now - int(r["mtime"])) / DAY < hi
        ]
        by_age.append({"age": label, "min_days": lo, "count": len(sel), "size": sum(_size(r) for r in sel)})
    out = _summary(rows, now, sample=10)
    out["by_age"] = by_age
    return out


def by_tag(files: Iterable[FileRow], tag: str, now: float, sample: int = 8) -> Dict[str, object]:
    return _summary([r for r in files if tag in tags_for(str(r["path"]))], now, sample)


def messaging(files: Iterable[FileRow], now: float) -> List[Dict[str, object]]:
    groups: Dict[str, Dict[str, List[FileRow]]] = defaultdict(lambda: defaultdict(list))
    for row in files:
        hit = classify_messaging(str(row["path"]))
        if hit:
            groups[hit[0]][hit[1]].append(row)
    out = []
    for app, types in groups.items():
        items = []
        for mtype, rows in types.items():
            s = _summary(rows, now, sample=5)
            s["type"] = mtype
            s["group"] = "messaging:%s:%s" % (app, mtype)
            items.append(s)
        items.sort(key=lambda s: -int(s["size"]))
        out.append({"app": app, "size": sum(int(s["size"]) for s in items), "count": sum(int(s["count"]) for s in items), "types": items})
    out.sort(key=lambda a: -int(a["size"]))
    return out


def empty_dirs(dirs: Iterable[str], files: Iterable[FileRow]) -> List[str]:
    """Folders that hold no file and no sub-folder."""
    has_child = set()
    for row in files:
        has_child.add(posixpath.dirname(str(row["path"])))
    all_dirs = list(dirs)
    for d in all_dirs:
        has_child.add(posixpath.dirname(d))
    out = []
    for d in all_dirs:
        if d in has_child:
            continue
        low = rel(d).casefold()
        if low.startswith("android/") or low == "android" or "/" not in low:
            continue  # system-made folders and the standard top-level ones
        out.append(d)
    return sorted(out)


def same_size_groups(files: Iterable[FileRow], min_size: int) -> List[List[FileRow]]:
    """Files that share a size. Only these are worth hashing."""
    by_size: Dict[int, List[FileRow]] = defaultdict(list)
    for row in files:
        s = row.get("size")
        if isinstance(s, int) and s >= max(1, min_size):
            by_size[s].append(row)
    return [g for g in by_size.values() if len(g) > 1]


def _keep_rank(row: FileRow) -> Tuple[int, int, str]:
    """Lower is better to keep: camera/gallery copies first, then oldest."""
    low = rel(str(row["path"])).casefold()
    if low.startswith("dcim/"):
        zone = 0
    elif low.startswith("pictures/") and classify_messaging(str(row["path"])) is None:
        zone = 1
    elif classify_messaging(str(row["path"])) is not None:
        zone = 3
    elif low.startswith("download/"):
        zone = 4
    else:
        zone = 2
    mtime = row.get("mtime")
    return (zone, int(mtime) if isinstance(mtime, int) else 0, str(row["path"]))


def duplicate_groups(groups: List[List[FileRow]], hashes: Dict[str, Optional[str]]) -> List[Dict[str, object]]:
    """Confirmed duplicates: same size and same content hash."""
    out = []
    for group in groups:
        by_hash: Dict[str, List[FileRow]] = defaultdict(list)
        for row in group:
            h = hashes.get(str(row["path"]))
            if h:
                by_hash[h].append(row)
        for h, rows in by_hash.items():
            if len(rows) < 2:
                continue
            rows = sorted(rows, key=_keep_rank)
            out.append(
                {
                    "hash": h,
                    "size": _size(rows[0]),
                    "keep": rows[0]["path"],
                    "extra": [_entry(r) for r in rows[1:]],
                    "wasted": _size(rows[0]) * (len(rows) - 1),
                }
            )
    out.sort(key=lambda g: -int(g["wasted"]))
    return out
