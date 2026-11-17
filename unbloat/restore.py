"""`restore`: switch back on everything unbloat switched off."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .adb import Adb
from .backup import backup_folders
from .journal import Journal, read_journal
from .packages.state import disabled_packages
from .util import human
from .workspace import Workspace


def disabled_by_us(entries: List[Dict[str, Any]]) -> List[str]:
    """Packages the journal says we switched off and have not switched back
    on, in the order they were switched off."""
    state: Dict[str, bool] = {}
    order: List[str] = []
    for e in entries:
        pkg = e.get("package")
        if not isinstance(pkg, str):
            continue
        if e.get("action") == "disable" and e.get("was_enabled"):
            if pkg in order:
                order.remove(pkg)
            order.append(pkg)
            state[pkg] = True
        elif e.get("action") == "enable" and e.get("result") == "ok":
            state[pkg] = False
        elif e.get("action") == "disable_result" and e.get("result") == "refused_by_phone":
            state[pkg] = False
    return [p for p in order if state.get(p)]


def run_restore(adb: Adb, workdir: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
    serial = adb.require_serial()
    ws = Workspace(workdir, serial)
    ours = disabled_by_us(read_journal(ws.journal_path))
    off_now = disabled_packages(adb)
    todo = [p for p in reversed(ours) if p in off_now]
    already = [p for p in ours if p not in off_now]
    data: Dict[str, Any] = {
        "dry_run": dry_run,
        "to_switch_on": todo,
        "already_on": already,
        "switched_on": [],
        "failed": [],
        "file_backups": backup_folders(ws),
    }
    if dry_run or not todo:
        return data
    journal = Journal(ws.journal_path, serial)
    for pkg in todo:
        cmd = ["pm", "enable", "--user", "0", pkg]
        adb.shell(cmd, retry=False)
        ok = pkg not in disabled_packages(adb)
        journal.record("enable", ["shell"] + cmd, "ok" if ok else "failed", package=pkg, reason="restore")
        (data["switched_on"] if ok else data["failed"]).append(pkg)
    return data


def format_restore(d: Dict[str, Any]) -> str:
    lines: List[str] = []
    if d["dry_run"]:
        if d["to_switch_on"]:
            lines.append("Would switch these apps back on (add --confirm to do it):")
            lines.extend("  " + p for p in d["to_switch_on"])
        else:
            lines.append("Nothing to switch back on: unbloat has no apps switched off on this phone.")
    else:
        lines.append("Switched back on: %s" % (", ".join(d["switched_on"]) or "nothing"))
        if d["failed"]:
            lines.append("Could NOT switch back on: %s" % ", ".join(d["failed"]))
    if d["already_on"]:
        lines.append("Already on again (the phone or you re-enabled them): %s" % ", ".join(d["already_on"]))
    lines.append("")
    if d["file_backups"]:
        lines.append("Copies of deleted files are on this computer. To put one back, copy it to the phone with")
        lines.append("your file manager, or: adb push <local file> <phone folder>")
        for b in d["file_backups"]:
            lines.append("  %s  (%d files, %s)" % (b["folder"], b["files"], human(b["size"])))
        lines.append("Each folder has a manifest.jsonl listing where every file came from.")
    else:
        lines.append("No file backups exist for this phone.")
    return "\n".join(lines)
