"""Keep the phone's gallery index in step after files are deleted.

On Android 11 and later, shared storage is served by the media provider
itself, so deleting a file through it removes the gallery entry at once
(checked on an Android 14 emulator). On older versions the index can keep a
ghost entry; there we ask the media provider to drop the row for each exact
path we deleted.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .adb import Adb, AdbError

FILES_URI = "content://media/external/file"
SAMPLE = 12


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def has_row(adb: Adb, path: str) -> Optional[bool]:
    """Does the media index still list this exact path? None if unknown."""
    try:
        res = adb.shell(
            ["content", "query", "--uri", FILES_URI, "--projection", "_id", "--where", "_data=" + _sql_text(path)],
            timeout=60,
        )
    except AdbError:
        return None
    if res.code != 0:
        return None
    text = res.text
    if "Row:" in text:
        return True
    if "No result found" in text:
        return False
    return None


def drop_row(adb: Adb, path: str) -> None:
    adb.shell(
        ["content", "delete", "--uri", FILES_URI, "--where", "_data=" + _sql_text(path)],
        timeout=60,
        retry=False,
    )


def refresh_media(adb: Adb, deleted: List[str]) -> Dict[str, object]:
    """Check a sample of deleted files for ghost gallery entries and clear
    them. Returns what was measured, never an assumption."""
    result: Dict[str, object] = {"checked": 0, "stale": 0, "cleared": 0, "unknown": False}
    if not deleted:
        return result
    sample = deleted[:SAMPLE]
    states = [has_row(adb, p) for p in sample]
    result["checked"] = len(sample)
    if all(s is None for s in states):
        result["unknown"] = True
        return result
    if not any(states):
        return result
    # Ghost entries exist on this phone: clear the row for every deleted path.
    cleared = 0
    for p in deleted:
        try:
            drop_row(adb, p)
            cleared += 1
        except AdbError:
            break
    result["cleared"] = cleared
    result["stale"] = sum(1 for p in sample if has_row(adb, p))
    return result
