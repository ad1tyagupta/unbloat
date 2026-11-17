"""One recursive listing of shared storage. Everything else is computed from it."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from ..adb import Adb
from ..paths import ROOT
from ..remote import stat_many

FileRow = Dict[str, object]  # {"path": str, "size": int, "mtime": int}


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def parse_printf_listing(raw: bytes) -> Tuple[List[FileRow], int]:
    """Parse `find -printf '%s\\t%T@\\t%p\\0'`. Returns (rows, bad_record_count)."""
    rows: List[FileRow] = []
    bad = 0
    for rec in raw.split(b"\x00"):
        if not rec:
            continue
        parts = rec.split(b"\t", 2)
        if len(parts) != 3:
            bad += 1
            continue
        try:
            size = int(parts[0])
            mtime = int(float(parts[1]))
        except ValueError:
            bad += 1
            continue
        rows.append({"path": _decode(parts[2]), "size": size, "mtime": mtime})
    return rows, bad


def parse_print0(raw: bytes) -> List[str]:
    return [_decode(r) for r in raw.split(b"\x00") if r]


def list_storage(
    adb: Adb,
    tools: Dict[str, bool],
    root: str = ROOT,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    """List every file and folder under shared storage. Read-only."""
    notes: List[str] = []
    say = progress or (lambda _m: None)
    if not tools.get("find"):
        return {"files": [], "dirs": [], "method": "none", "notes": ["This phone has no 'find' tool, so files could not be listed."]}
    say("Listing files on the phone...")
    if tools.get("find_printf"):
        res = adb.shell(["find", root, "-type", "f", "-printf", "%s\\t%T@\\t%p\\0"], timeout=900)
        files, bad = parse_printf_listing(res.stdout)
        method = "find-printf"
        if bad:
            notes.append("%d entries in the file listing could not be read and were skipped." % bad)
    else:
        res = adb.shell(["find", root, "-type", "f", "-print0"], timeout=900)
        names = parse_print0(res.stdout)
        say("Reading sizes of %d files (slower on this phone)..." % len(names))
        files = []
        if tools.get("stat_c"):
            for name, info in zip(names, stat_many(adb, names)):
                if info is not None:
                    files.append({"path": name, "size": info["size"], "mtime": info["mtime"]})
            method = "find-print0+stat"
        else:
            files = [{"path": n, "size": None, "mtime": None} for n in names]
            method = "find-print0"
            notes.append("This phone cannot report file sizes, so size-based findings are unavailable.")
    denied = res.err.count("Permission denied")
    if denied:
        notes.append("%d folders could not be opened (the phone does not allow it) and are not counted." % denied)
    dres = adb.shell(["find", root, "-type", "d", "-print0"], timeout=600)
    dirs = [d for d in parse_print0(dres.stdout) if d != root]
    say("Found %d files in %d folders." % (len(files), len(dirs)))
    return {"files": files, "dirs": dirs, "method": method, "notes": notes}
