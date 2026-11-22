"""`pick`: turn a group from the last scan into a plan batch of exact paths.

The assistant chooses a group ("installers", "duplicates", ...) and filters;
this code writes the precise file list. Nothing is matched on the phone by
pattern: the plan ends up holding plain paths, each checked again at apply.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .adb import Adb
from .errors import Refused
from .journal import read_journal
from .packages.state import list_packages
from .paths import PathRejected, normalize_remote, personal_zone
from .plan import MAX_FILES_PER_BATCH, PLAN_VERSION
from .restore import disabled_by_us
from .storage.analyze import tags_for
from .util import human, show
from .workspace import Workspace, read_json


def _load_or_new(plan_file: str, serial: str) -> Dict[str, Any]:
    if os.path.exists(plan_file):
        with open(plan_file, "r", encoding="utf-8") as fh:
            plan = json.load(fh)
        if not isinstance(plan, dict) or plan.get("serial") != serial:
            raise Refused("That plan file belongs to a different phone.", [])
        return plan
    return {"version": PLAN_VERSION, "serial": serial, "batches": []}


def _next_id(plan: Dict[str, Any], stem: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]", "-", stem)[:30].strip("-") or "batch"
    used = {b.get("id") for b in plan["batches"] if isinstance(b, dict)}
    n = 1
    while "%s-%d" % (stem, n) in used:
        n += 1
    return "%s-%d" % (stem, n)


def _save(plan_file: str, plan: Dict[str, Any]) -> None:
    tmp = plan_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1, ensure_ascii=True)
    os.replace(tmp, plan_file)


def run_pick(
    adb: Adb,
    group: str,
    plan_file: str,
    workdir: Optional[str] = None,
    older_than_days: Optional[int] = None,
    min_size_mb: Optional[float] = None,
    title: Optional[str] = None,
    user_saw_sample: bool = False,
    no_backup: bool = False,
) -> Dict[str, Any]:
    serial = adb.require_serial()
    ws = Workspace.for_device(workdir, adb)
    plan = _load_or_new(plan_file, serial)
    already = {
        f.get("path") for b in plan["batches"] if isinstance(b, dict) and b.get("kind") == "delete_files"
        for f in b.get("files", []) if isinstance(f, dict)
    }

    if group == "caches":
        batch: Dict[str, Any] = {"id": _next_id(plan, "caches"), "kind": "trim_caches", "title": title or "Clear app caches"}
        plan["batches"].append(batch)
        _save(plan_file, plan)
        return {"plan": plan_file, "batch": batch["id"], "kind": "trim_caches", "count": 0, "size": 0, "sample": []}

    if group == "redisable":
        ours = disabled_by_us(read_journal(ws.journal_path))
        packages = list_packages(adb)
        again = [p for p in ours if p in packages and packages[p]["enabled"]]
        if not again:
            raise Refused("Nothing to redo: every app unbloat switched off is still off.", [])
        batch = {
            "id": _next_id(plan, "redisable"),
            "kind": "disable_packages",
            "title": title or "Switch off again the apps that came back",
            "packages": again,
            "user_named": [],
            "caution_explained": False,
        }
        plan["batches"].append(batch)
        _save(plan_file, plan)
        return {"plan": plan_file, "batch": batch["id"], "kind": "disable_packages", "packages": again, "count": len(again),
                "note": "Tiers are checked again at the dry run. Set caution_explained / user_named as before if needed."}

    index_path = ws.latest("scans", "storageindex")
    if not index_path:
        raise Refused("There is no storage scan yet.", ["Run: python -m unbloat scan storage"])
    index = read_json(index_path)
    now = float(index.get("now") or 0)
    dup_extra = set(index.get("duplicate_extra") or [])
    keep_of: Dict[str, str] = index.get("duplicate_keep") or {}
    kept = set(keep_of.values())
    extras_in_plan_keep = {keep_of[p] for p in already if p in keep_of}
    held_back = 0
    chosen: List[Dict[str, Any]] = []
    for path, size, mtime in index["files"]:
        if group == "duplicates":
            if path not in dup_extra:
                continue
        elif group not in tags_for(path):
            continue
        if older_than_days is not None:
            if not isinstance(mtime, int) or (now - mtime) / 86400 < older_than_days:
                continue
        if min_size_mb is not None:
            if not isinstance(size, int) or size < min_size_mb * 1024 * 1024:
                continue
        try:
            norm = normalize_remote(path)
        except PathRejected:
            continue
        if norm in already:
            continue
        # Never let one plan remove both a file and the copy that was meant
        # to be kept in its place.
        if group == "duplicates" and keep_of.get(path) in already:
            held_back += 1
            continue
        if group != "duplicates" and path in kept and path in extras_in_plan_keep:
            held_back += 1
            continue
        chosen.append({"path": norm, "size": size, "mtime": mtime})
    if not chosen:
        raise Refused(
            "No files in the last scan match that group and those filters.",
            ["%d were left out because the plan already removes their only other copy." % held_back] if held_back else [],
        )
    chosen.sort(key=lambda f: -(f["size"] or 0))
    zones = sorted({z for z in (personal_zone(f["path"]) for f in chosen) if z})
    made = []
    preview_only = bool(zones) and not user_saw_sample
    for start in range(0, len(chosen), MAX_FILES_PER_BATCH):
        if preview_only:
            break
        part = chosen[start:start + MAX_FILES_PER_BATCH]
        batch = {
            "id": _next_id(plan, group.split(":")[0]),
            "kind": "delete_files",
            "title": title or group,
            "category": group,
            "backup": not no_backup,
            "user_saw_sample": bool(user_saw_sample),
            "files": part,
        }
        plan["batches"].append(batch)
        made.append(batch["id"])
    if made:
        _save(plan_file, plan)
    data: Dict[str, Any] = {
        "plan": plan_file,
        "batches": made,
        "kind": "delete_files",
        "group": group,
        "count": len(chosen),
        "size": sum(f["size"] or 0 for f in chosen),
        "sample": chosen[:10],
        "personal_zones": zones,
        "from_scan": index_path,
        "held_back": held_back,
    }
    if held_back:
        data["held_back_note"] = (
            "%d files were left out because the plan already removes their only other copy." % held_back
        )
    if zones and not user_saw_sample:
        data["added"] = False
        data["needs"] = (
            "NOT added to the plan yet. These files are in %s. Show the user the count, total size and this "
            "sample. After they agree, run the same pick command again with --user-saw-sample." % " and ".join(zones)
        )
    return data


def format_pick(d: Dict[str, Any]) -> str:
    if d["kind"] == "trim_caches":
        return "Added a clear-app-caches step to %s" % d["plan"]
    if d["kind"] == "disable_packages":
        return "Added %d apps to switch off again to %s:\n  %s" % (d["count"], d["plan"], "\n  ".join(d["packages"]))
    verb = "Found" if d.get("added") is False else "Added"
    lines = ["%s %d files (%s) from group '%s' for %s" % (verb, d["count"], human(d["size"]), show(d["group"]), d["plan"])]
    for f in d["sample"]:
        lines.append("  %10s  %s" % (human(f["size"]), show(f["path"], 100)))
    if d["count"] > len(d["sample"]):
        lines.append("  ... and %d more" % (d["count"] - len(d["sample"])))
    if d.get("needs"):
        lines.append("")
        lines.append(d["needs"])
    if d.get("added") is not False:
        lines.append("")
        lines.append("Next: python -m unbloat plan %s" % d["plan"])
    return "\n".join(lines)
