"""Snapshots taken before any change, and safe local copies of phone files."""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Dict, List, Optional

from .adb import Adb
from .device import device_info
from .packages.state import list_packages, read_defaults
from .paths import relative
from .storage.scan import read_space
from .util import human
from .workspace import Workspace

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def take_snapshot(adb: Adb, ws: Workspace) -> Dict[str, Any]:
    """Record package states, default apps and free space. Read-only on the phone."""
    info = device_info(adb)
    packages = list_packages(adb)
    snap = {
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "device": info,
        "space": read_space(adb),
        "defaults": read_defaults(adb),
        "packages": packages,
    }
    path = ws.write_json("backups", "state", snap)
    snap["saved"] = path
    return snap


def run_backup(adb: Adb, workdir: Optional[str] = None) -> Dict[str, Any]:
    ws = Workspace(workdir, adb.require_serial())
    snap = take_snapshot(adb, ws)
    pk = snap["packages"]
    return {
        "saved": snap["saved"],
        "space": snap["space"],
        "defaults": snap["defaults"],
        "package_count": len(pk),
        "disabled_count": sum(1 for p in pk.values() if not p["enabled"]),
    }


def format_backup(d: Dict[str, Any]) -> str:
    lines = ["Saved the phone's current state to %s" % d["saved"]]
    lines.append("%d apps recorded, %d of them already switched off" % (d["package_count"], d["disabled_count"]))
    df = d["defaults"]
    lines.append(
        "Home screen: %s | Keyboard: %s | Phone: %s | Messages: %s"
        % (df.get("launcher"), df.get("keyboard"), ", ".join(df.get("dialer") or []) or None, ", ".join(df.get("sms") or []) or None)
    )
    if d.get("space"):
        lines.append("Free space now: %s" % human(d["space"]["free"]))
    return "\n".join(lines)


def _clean_part(part: str) -> str:
    part = part.encode("utf-8", "replace").decode("utf-8")
    part = _BAD.sub("_", part).rstrip(". ")
    if not part:
        part = "_"
    stem = part.split(".", 1)[0].upper()
    if stem in _RESERVED:
        part = "_" + part
    if len(part) > 120:
        base, dot, ext = part.rpartition(".")
        if dot and len(ext) <= 10:
            part = base[: 120 - len(ext) - 1] + "." + ext
        else:
            part = part[:120]
    return part


def local_path_for(folder: str, remote: str, taken: set) -> str:
    """A safe file name on this computer for a phone path.

    Phone names may be illegal on Windows or macOS, so they are cleaned; the
    manifest keeps the real phone path next to the local one.
    """
    parts = [_clean_part(p) for p in relative(remote).split("/")]
    candidate = os.path.join(folder, *parts)
    if len(candidate) > 230:
        digest = hashlib.sha1(remote.encode("utf-8", "surrogateescape")).hexdigest()
        ext = os.path.splitext(parts[-1])[1][:11]
        candidate = os.path.join(folder, "_long", digest + ext)
    base, ext = os.path.splitext(candidate)
    n = 1
    while candidate.lower() in taken or os.path.exists(candidate):
        n += 1
        candidate = "%s~%d%s" % (base, n, ext)
    taken.add(candidate.lower())
    return candidate


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def free_disk(path: str) -> int:
    import shutil

    probe = path
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe).free


def backup_folders(ws: Workspace) -> List[Dict[str, Any]]:
    """Where pulled copies live, for `restore` and `report`."""
    root = os.path.join(ws.root, "backups", "files")
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        count = 0
        size = 0
        for base, _dirs, files in os.walk(folder):
            for f in files:
                if f == "manifest.jsonl":
                    continue
                count += 1
                try:
                    size += os.path.getsize(os.path.join(base, f))
                except OSError:
                    pass
        out.append({"folder": folder, "files": count, "size": size, "manifest": os.path.join(folder, "manifest.jsonl")})
    return out
