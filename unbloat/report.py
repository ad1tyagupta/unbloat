"""`report`: what changed, with measured numbers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from .adb import Adb
from .backup import backup_folders
from .journal import read_journal
from .packages.state import disabled_packages
from .restore import disabled_by_us
from .storage.scan import read_space
from .util import human
from .workspace import Workspace, read_json


def run_report(adb: Adb, workdir: Optional[str] = None) -> Dict[str, Any]:
    serial = adb.require_serial()
    ws = Workspace(workdir, serial)
    entries = read_journal(ws.journal_path)
    by_cat: Dict[str, Dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    trimmed = 0
    trim_runs = 0
    trim_unmeasured = 0
    failed_deletes = 0
    for e in entries:
        if e.get("action") == "delete":
            if e.get("result") == "ok":
                c = by_cat[str(e.get("category") or "other")]
                c["files"] += 1
                c["bytes"] += int(e.get("size") or 0)
            else:
                failed_deletes += 1
        elif e.get("action") == "trim_caches":
            trim_runs += 1
            if e.get("freed") is None:
                trim_unmeasured += 1
            else:
                trimmed += int(e["freed"])
    ours = disabled_by_us(entries)
    off_now = disabled_packages(adb)
    first = ws.first("backups", "state")
    before = read_json(first).get("space") if first else None
    now = read_space(adb)
    data: Dict[str, Any] = {
        "serial": serial,
        "first_snapshot": first,
        "free_before": before["free"] if before else None,
        "free_now": now["free"] if now else None,
        "free_change_measured": (now["free"] - before["free"]) if (before and now) else None,
        "deleted_by_category": dict(by_cat),
        "deleted_files": sum(c["files"] for c in by_cat.values()),
        "deleted_bytes": sum(c["bytes"] for c in by_cat.values()),
        "deletes_that_failed": failed_deletes,
        "cache_trim": {"runs": trim_runs, "freed_measured": trimmed, "runs_not_measurable": trim_unmeasured},
        "apps_switched_off": [p for p in ours if p in off_now],
        "apps_switched_back_on_by_phone": [p for p in ours if p not in off_now],
        "file_backups": backup_folders(ws),
        "journal": ws.journal_path,
        "how_to_undo": "python -m unbloat restore --confirm",
        "caveat": "The free-space change is measured on the phone and includes anything else that "
        "happened in between (new photos, app updates). The per-category numbers are exact sizes of files deleted.",
    }
    return data


def format_report(d: Dict[str, Any]) -> str:
    lines: List[str] = []
    if d["free_change_measured"] is not None:
        lines.append(
            "Free space on the phone: %s before, %s now (%s)"
            % (human(d["free_before"]), human(d["free_now"]), human(d["free_change_measured"]) + " change, measured")
        )
    elif d["free_now"] is not None:
        lines.append("Free space on the phone now: %s (no 'before' snapshot to compare with)" % human(d["free_now"]))
    lines.append("Files deleted: %d, %s" % (d["deleted_files"], human(d["deleted_bytes"])))
    for cat, c in sorted(d["deleted_by_category"].items(), key=lambda kv: -kv[1]["bytes"]):
        lines.append("  %-30s %6d files  %10s" % (cat, c["files"], human(c["bytes"])))
    t = d["cache_trim"]
    if t["runs"]:
        lines.append("App caches cleared %d time(s): %s measured" % (t["runs"], human(t["freed_measured"])))
    lines.append("Apps switched off: %d" % len(d["apps_switched_off"]))
    lines.extend("  " + p for p in d["apps_switched_off"])
    if d["apps_switched_back_on_by_phone"]:
        lines.append("Switched back on since (by the phone, an update, or you):")
        lines.extend("  " + p for p in d["apps_switched_back_on_by_phone"])
    if d["file_backups"]:
        lines.append("Copies of deleted files:")
        for b in d["file_backups"]:
            lines.append("  %s  (%d files, %s)" % (b["folder"], b["files"], human(b["size"])))
    lines.append("Record of every action: %s" % d["journal"])
    lines.append("To switch apps back on: %s" % d["how_to_undo"])
    lines.append(d["caveat"])
    return "\n".join(lines)
