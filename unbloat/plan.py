"""Plan files: what the assistant proposes and the user approves.

A plan is a JSON file listing exact actions. This module checks its shape and
every rule that can be checked without the phone. Checks that need the phone
are in `apply.py`. Unknown keys are refused, so a plan cannot carry a field
like "skip_checks" and hope something honours it.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Tuple

from .packages.denylist import valid_package_name
from .paths import PathRejected, normalize_remote, personal_zone

PLAN_VERSION = 1
KINDS = ("delete_files", "trim_caches", "disable_packages")
MAX_FILES_PER_BATCH = 5000
MAX_PACKAGES_PER_BATCH = 200

_TOP_KEYS = {"version", "serial", "note", "batches"}
_BATCH_KEYS = {
    "delete_files": {"id", "kind", "title", "category", "files", "backup", "user_saw_sample"},
    "trim_caches": {"id", "kind", "title"},
    "disable_packages": {"id", "kind", "title", "packages", "user_named", "caution_explained"},
}
_FILE_KEYS = {"path", "size", "mtime"}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class PlanError(Exception):
    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def load_plan(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise PlanError(["cannot open the plan file: %s" % exc])
    except ValueError as exc:
        raise PlanError(["the plan file is not valid JSON: %s" % exc])
    if not isinstance(doc, dict):
        raise PlanError(["the plan must be a JSON object"])
    return doc


def approval_code(plan: Dict[str, Any]) -> str:
    """A short code tied to the exact contents of the plan.

    The dry run prints it; `apply --confirm` must repeat it. Change one byte
    of the plan after the dry run and the code no longer matches.
    """
    canon = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("ascii")).hexdigest()[:10]


def check_plan(plan: Dict[str, Any], serial: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate everything that needs no phone.

    Returns (batches with normalized paths, problems). Any problem means the
    whole plan is refused.
    """
    problems: List[str] = []
    extra = set(plan) - _TOP_KEYS
    if extra:
        problems.append("unknown keys at the top of the plan: %s" % ", ".join(sorted(extra)))
    if plan.get("version") != PLAN_VERSION:
        problems.append("plan 'version' must be %d" % PLAN_VERSION)
    if plan.get("serial") != serial:
        problems.append(
            "this plan was written for phone %r but the connected phone is %r" % (plan.get("serial"), serial)
        )
    batches = plan.get("batches")
    if not isinstance(batches, list) or not batches:
        problems.append("the plan has no batches")
        return [], problems
    out: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_paths = set()
    seen_pkgs = set()
    for n, batch in enumerate(batches):
        where = "batch %d" % (n + 1)
        if not isinstance(batch, dict):
            problems.append("%s: must be an object" % where)
            continue
        bid = batch.get("id")
        if not isinstance(bid, str) or not _ID.match(bid):
            problems.append("%s: 'id' must be 1-40 letters, digits, '-' or '_'" % where)
            continue
        where = "batch %s" % bid
        if bid in seen_ids:
            problems.append("%s: id used twice" % where)
        seen_ids.add(bid)
        kind = batch.get("kind")
        if kind not in KINDS:
            problems.append("%s: 'kind' must be one of %s (nothing else can be done)" % (where, ", ".join(KINDS)))
            continue
        extra = set(batch) - _BATCH_KEYS[kind]
        if extra:
            problems.append("%s: unknown keys %s" % (where, ", ".join(sorted(extra))))
        title = batch.get("title", "")
        if not isinstance(title, str):
            problems.append("%s: 'title' must be text" % where)
            title = ""
        clean: Dict[str, Any] = {"id": bid, "kind": kind, "title": title}
        if kind == "delete_files":
            files = batch.get("files")
            if not isinstance(files, list) or not files:
                problems.append("%s: 'files' must be a non-empty list" % where)
                continue
            if len(files) > MAX_FILES_PER_BATCH:
                problems.append("%s: more than %d files; split it into smaller batches" % (where, MAX_FILES_PER_BATCH))
                continue
            backup = batch.get("backup", True)
            if not isinstance(backup, bool):
                problems.append("%s: 'backup' must be true or false" % where)
                backup = True
            saw = batch.get("user_saw_sample", False)
            if not isinstance(saw, bool):
                problems.append("%s: 'user_saw_sample' must be true or false" % where)
                saw = False
            category = batch.get("category", "other")
            if not isinstance(category, str) or len(category) > 80:
                problems.append("%s: 'category' must be short text" % where)
                category = "other"
            rows = []
            zones = set()
            for i, f in enumerate(files):
                if not isinstance(f, dict) or set(f) - _FILE_KEYS:
                    problems.append("%s file %d: must be an object with only path, size, mtime" % (where, i + 1))
                    continue
                try:
                    path = normalize_remote(f.get("path"))
                except PathRejected as exc:
                    problems.append("%s file %d: %s" % (where, i + 1, exc))
                    continue
                if path in seen_paths:
                    problems.append("%s file %d: the same file is listed twice in the plan" % (where, i + 1))
                    continue
                seen_paths.add(path)
                size = f.get("size")
                mtime = f.get("mtime")
                if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
                    problems.append("%s file %d: 'size' must be a whole number" % (where, i + 1))
                    continue
                if mtime is not None and (not isinstance(mtime, int) or isinstance(mtime, bool)):
                    problems.append("%s file %d: 'mtime' must be a whole number" % (where, i + 1))
                    continue
                zone = personal_zone(path)
                if zone:
                    zones.add(zone)
                rows.append({"path": path, "size": size, "mtime": mtime})
            if zones and not saw:
                problems.append(
                    "%s: deletes from %s. The user must first be shown how many files, the total size and "
                    "a sample; then set \"user_saw_sample\": true" % (where, " and ".join(sorted(zones)))
                )
            clean.update({"files": rows, "backup": backup, "category": category, "zones": sorted(zones)})
        elif kind == "disable_packages":
            pkgs = batch.get("packages")
            if not isinstance(pkgs, list) or not pkgs:
                problems.append("%s: 'packages' must be a non-empty list" % where)
                continue
            if len(pkgs) > MAX_PACKAGES_PER_BATCH:
                problems.append("%s: too many packages in one batch" % where)
                continue
            good = []
            for p in pkgs:
                if not valid_package_name(p):
                    problems.append("%s: %r is not a package name" % (where, p))
                elif p in seen_pkgs:
                    problems.append("%s: %s is listed twice in the plan" % (where, p))
                else:
                    seen_pkgs.add(p)
                    good.append(p)
            named = batch.get("user_named", [])
            if not isinstance(named, list) or any(p not in pkgs for p in named):
                problems.append("%s: 'user_named' must be a list of packages from this batch" % where)
                named = []
            explained = batch.get("caution_explained", False)
            if not isinstance(explained, bool):
                problems.append("%s: 'caution_explained' must be true or false" % where)
                explained = False
            clean.update({"packages": good, "user_named": list(named), "caution_explained": explained})
        out.append(clean)
    return out, problems
