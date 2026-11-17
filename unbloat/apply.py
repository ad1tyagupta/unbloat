"""Check a plan against the phone, and carry it out.

`run_apply` with no confirmation code is the dry run: it validates everything
and changes nothing. With the right code it executes, journaling every step.
There is no option that skips a check.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .adb import Adb, AdbError
from .backup import free_disk, local_path_for, md5_file, sha256_file, take_snapshot
from .errors import Refused
from .device import device_info, probe_tools
from .journal import Journal
from .media import refresh_media
from .packages.kb import lookup
from .packages.state import (
    disabled_packages,
    essentials_missing,
    list_packages,
    protected_by_defaults,
    read_defaults,
)
from .plan import PlanError, approval_code, check_plan, load_plan
from .remote import canonical, chunked, delete_file, hash_many, pick_hash_tool, stat_many, storage_root_ok
from .storage.scan import read_space
from .util import human, show
from .workspace import Workspace, stamp

DISABLE_GROUP = 5
TRIM_TARGET = "999999999999999"


class Interrupted(AdbError):
    kind = "interrupted"


# -- inspection (read-only) ------------------------------------------------

def _canonical_many(adb: Adb, tools: Dict[str, bool], paths: Sequence[str]) -> List[Optional[str]]:
    """Resolve many paths. Falls back to one call per path when a batch
    answer cannot be matched up line for line."""
    out: List[Optional[str]] = []
    tool = ["realpath"] if tools.get("realpath") else (["readlink", "-f"] if tools.get("readlink") else None)
    if tool is None:
        return [None for _ in paths]
    simple = [p for p in paths if "\n" not in p and "\r" not in p]
    answers: Dict[str, Optional[str]] = {}
    for chunk in chunked(simple):
        res = adb.shell(tool + chunk, timeout=120)
        lines = res.text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if res.code == 0 and len(lines) == len(chunk):
            answers.update(zip(chunk, lines))
        else:
            for p in chunk:
                answers[p] = canonical(adb, tools, p)
    for p in paths:
        out.append(answers[p] if p in answers else canonical(adb, tools, p))
    return out


def _inspect_files(
    adb: Adb, tools: Dict[str, bool], files: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Sort a batch's files into (ready, skipped, refusals) by asking the phone."""
    ready: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    refusals: List[str] = []
    paths = [f["path"] for f in files]
    infos = stat_many(adb, paths)
    present = [p for p, i in zip(paths, infos) if i is not None]
    reals = dict(zip(present, _canonical_many(adb, tools, present)))
    for f, info in zip(files, infos):
        path = f["path"]
        if info is None:
            skipped.append({"path": path, "why": "no longer on the phone"})
            continue
        if not str(info["type"]).startswith("regular"):
            refusals.append("%s is not a plain file (it is a %s)" % (show(path), info["type"]))
            continue
        real = reals.get(path)
        if real is None:
            refusals.append("the phone could not confirm where %s really points" % show(path))
            continue
        if real != path:
            refusals.append("%s points somewhere else on the phone (symlink)" % show(path))
            continue
        if f.get("size") is not None and f["size"] != info["size"]:
            skipped.append({"path": path, "why": "changed since the plan was written (size differs)"})
            continue
        if f.get("mtime") is not None and f["mtime"] != info["mtime"]:
            skipped.append({"path": path, "why": "changed since the plan was written (modified later)"})
            continue
        ready.append({"path": path, "size": int(info["size"]), "mtime": int(info["mtime"])})  # type: ignore[arg-type]
    return ready, skipped, refusals


def _inspect_packages(
    batch: Dict[str, Any],
    packages: Dict[str, Dict[str, object]],
    protected: Dict[str, str],
    family: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    refusals: List[str] = []
    for pkg in batch["packages"]:
        know = lookup(pkg, family)
        row: Dict[str, Any] = {"package": pkg, "name": know["name"], "tier": know["tier"], "breaks": know["breaks"]}
        if pkg not in packages:
            row.update(action="skip", why="not installed on this phone")
        elif not packages[pkg]["enabled"]:
            row.update(action="skip", why="already switched off")
        elif know["tier"] == "never":
            row.update(action="refuse")
            refusals.append("%s can never be switched off: %s" % (pkg, know["breaks"] or know["what"]))
        elif pkg in protected:
            row.update(action="refuse")
            refusals.append("%s cannot be switched off because %s" % (pkg, protected[pkg]))
        elif know["tier"] in ("risky", "unknown") and pkg not in batch["user_named"]:
            row.update(action="refuse")
            refusals.append(
                "%s is %s. It may only be switched off when the user asked about this exact app by name "
                "(then list it under \"user_named\")" % (pkg, "rated risky" if know["tier"] == "risky" else "not in the knowledge base")
            )
        elif know["tier"] == "caution" and not batch["caution_explained"]:
            row.update(action="refuse")
            refusals.append(
                "%s is rated caution (%s). Explain that to the user first, then set \"caution_explained\": true"
                % (pkg, know["breaks"])
            )
        else:
            row.update(action="disable")
        rows.append(row)
    return rows, refusals


def inspect_plan(
    adb: Adb, tools: Dict[str, bool], info: Dict[str, object], batches: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    views: List[Dict[str, Any]] = []
    refusals: List[str] = []
    has_delete = any(b["kind"] == "delete_files" for b in batches)
    has_disable = any(b["kind"] == "disable_packages" for b in batches)
    if has_delete:
        if not tools.get("stat_c") or not tools.get("rm"):
            refusals.append("this phone lacks the tools needed to check files safely before deleting")
        elif not storage_root_ok(adb, tools):
            refusals.append("the phone's shared storage does not resolve to the main user's storage; refusing to delete")
    packages: Dict[str, Dict[str, object]] = {}
    protected: Dict[str, str] = {}
    if has_disable:
        packages = list_packages(adb)
        defaults = read_defaults(adb)
        missing = essentials_missing(defaults)
        if missing:
            refusals.append(
                "could not work out the phone's current %s, so nothing will be switched off" % " and ".join(missing)
            )
        protected = protected_by_defaults(defaults)
    for b in batches:
        view: Dict[str, Any] = {"id": b["id"], "kind": b["kind"], "title": b["title"]}
        if b["kind"] == "delete_files":
            if refusals and not has_disable:
                views.append(view)
                continue
            ready, skipped, bad = _inspect_files(adb, tools, b["files"])
            refusals.extend("batch %s: %s" % (b["id"], r) for r in bad)
            ready.sort(key=lambda f: -f["size"])
            view.update(
                category=b["category"],
                backup=b["backup"],
                zones=b["zones"],
                count=len(ready),
                size=sum(f["size"] for f in ready),
                sample=[{"path": f["path"], "size": f["size"]} for f in ready[:8]],
                skipped=skipped[:50],
                skipped_count=len(skipped),
                _ready=ready,
            )
        elif b["kind"] == "disable_packages":
            rows, bad = _inspect_packages(b, packages, protected, str(info.get("family")))
            refusals.extend("batch %s: %s" % (b["id"], r) for r in bad)
            view.update(packages=rows, count=sum(1 for r in rows if r["action"] == "disable"))
        else:
            view.update(note="Asks Android to clear apps' temporary cache files. No logins or app data are lost.")
        views.append(view)
    return views, refusals


# -- execution -------------------------------------------------------------

def _pull_timeout(size: int) -> float:
    return 120 + size / (300 * 1024)


def _delete_batch(
    adb: Adb,
    tools: Dict[str, bool],
    ws: Workspace,
    journal: Journal,
    view: Dict[str, Any],
    folder: str,
    say: Callable[[str], None],
) -> Dict[str, Any]:
    ready: List[Dict[str, Any]] = view["_ready"]
    backup: bool = view["backup"]
    tool = pick_hash_tool(tools)
    taken: set = set()
    manifest = os.path.join(folder, "manifest.jsonl")
    deleted: List[str] = []
    freed = 0
    failed: List[Dict[str, str]] = []
    total = len(ready)
    done = 0
    for start in range(0, total, 25):
        group = ready[start:start + 25]
        paths = [f["path"] for f in group]
        # Re-check on the phone right before acting on each small group.
        infos = stat_many(adb, paths)
        reals = _canonical_many(adb, tools, paths)
        ok: List[Dict[str, Any]] = []
        for f, info, real in zip(group, infos, reals):
            if info is None:
                failed.append({"path": f["path"], "why": "disappeared before it could be handled"})
            elif not str(info["type"]).startswith("regular") or real != f["path"]:
                failed.append({"path": f["path"], "why": "is no longer a plain file at that location"})
            elif info["size"] != f["size"] or info["mtime"] != f["mtime"]:
                failed.append({"path": f["path"], "why": "changed while the plan was running"})
            else:
                ok.append(f)
        hashes: Dict[str, Optional[str]] = {}
        if backup and tool and ok:
            hashes = hash_many(adb, tool, [f["path"] for f in ok], {f["path"]: f["size"] for f in ok})
        for f in ok:
            path = f["path"]
            local: Optional[str] = None
            digest: Optional[str] = None
            if backup:
                local = local_path_for(folder, path, taken)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                why = _pull_verified(adb, tool, path, f["size"], hashes.get(path), local)
                if why:
                    if os.path.exists(local):
                        os.remove(local)
                    failed.append({"path": path, "why": "backup copy failed (%s); the file was NOT deleted" % why})
                    journal.record("delete", ["pull", path], "backup_failed", path=path, batch=view["id"], why=why)
                    continue
                digest = sha256_file(local)
                with open(manifest, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"phone_path": path, "local": local, "size": f["size"], "sha256": digest}) + "\n")
            gone = delete_file(adb, path)
            journal.record(
                "delete",
                ["shell", "rm", path],
                "ok" if gone else "failed",
                path=path,
                size=f["size"],
                batch=view["id"],
                category=view["category"],
                backup=local,
                sha256=digest,
            )
            if gone:
                deleted.append(path)
                freed += f["size"]
            else:
                failed.append({"path": path, "why": "the phone did not delete it"})
        done += len(group)
        say("  %s: %d of %d files handled" % (view["id"], done, total))
    media = refresh_media(adb, deleted)
    return {
        "deleted": len(deleted),
        "bytes_deleted": freed,
        "failed": failed[:100],
        "failed_count": len(failed),
        "backup_folder": folder if backup else None,
        "media_index": media,
    }


def _pull_verified(adb: Adb, tool: Optional[str], path: str, size: int, remote_hash: Optional[str], local: str) -> Optional[str]:
    """Copy one file and prove the copy is identical. Returns None on success,
    otherwise a short reason."""
    res = adb.pull(path, local, timeout=_pull_timeout(size))
    if res.code != 0 or not os.path.isfile(local):
        return "could not copy the file"
    if os.path.getsize(local) != size:
        return "copy has the wrong size"
    if tool:
        if not remote_hash:
            return "the phone could not fingerprint the file"
        mine = sha256_file(local) if tool == "sha256sum" else md5_file(local)
        if mine != remote_hash:
            return "copy does not match the original"
        return None
    # No fingerprint tool on the phone: copy a second time and compare the two.
    second = local + ".verify"
    try:
        res = adb.pull(path, second, timeout=_pull_timeout(size))
        if res.code != 0 or not os.path.isfile(second):
            return "could not copy the file a second time to verify it"
        if sha256_file(second) != sha256_file(local):
            return "two copies of the file did not match"
    finally:
        if os.path.exists(second):
            os.remove(second)
    return None


def _systemui_pid(adb: Adb) -> str:
    return adb.shell_text(["pidof", "com.android.systemui"]).strip()


def health_check(adb: Adb, pid_before: str, sleep: Callable[[float], None]) -> Optional[str]:
    """None if the phone looks healthy, otherwise what is wrong."""
    try:
        for attempt in range(3):
            sleep(3 if attempt == 0 else 2)
            booted = adb.shell_text(["getprop", "sys.boot_completed"]).strip()
            pid = _systemui_pid(adb)
            if booted == "1" and pid:
                if pid_before and pid != pid_before:
                    return "the phone's screen software restarted"
                return None
        if booted != "1":
            return "the phone is no longer reporting that it has finished starting up"
        return "the phone's screen software stopped running"
    except AdbError as exc:
        return "the phone stopped answering (%s)" % exc.message


def _disable_batch(
    adb: Adb, journal: Journal, view: Dict[str, Any], sleep: Callable[[float], None], say: Callable[[str], None]
) -> Dict[str, Any]:
    todo = [r["package"] for r in view["packages"] if r["action"] == "disable"]
    disabled: List[str] = []
    refused: List[str] = []
    pid_before = _systemui_pid(adb)
    for start in range(0, len(todo), DISABLE_GROUP):
        group = todo[start:start + DISABLE_GROUP]
        for pkg in group:
            cmd = ["pm", "disable-user", "--user", "0", pkg]
            journal.record("disable", ["shell"] + cmd, "attempted", package=pkg, batch=view["id"], was_enabled=True)
            adb.shell(cmd, retry=False)
        problem = health_check(adb, pid_before, sleep)
        if problem:
            undone = []
            for pkg in group:
                cmd = ["pm", "enable", "--user", "0", pkg]
                try:
                    adb.shell(cmd, retry=False)
                    journal.record("enable", ["shell"] + cmd, "ok", package=pkg, reason="rollback")
                    undone.append(pkg)
                except AdbError as exc:
                    journal.record("enable", ["shell"] + cmd, "failed", package=pkg, reason="rollback", why=exc.message)
            return {
                "disabled": disabled,
                "refused_by_phone": refused,
                "rolled_back": group,
                "rolled_back_ok": undone,
                "stopped": "After switching off %s, %s. Those apps were switched back on and unbloat stopped."
                % (", ".join(group), problem),
            }
        now_off = disabled_packages(adb)
        for pkg in group:
            if pkg in now_off:
                disabled.append(pkg)
                journal.record("disable_result", None, "ok", package=pkg, batch=view["id"])
            else:
                refused.append(pkg)
                journal.record("disable_result", None, "refused_by_phone", package=pkg, batch=view["id"])
        say("  %s: %d of %d apps handled" % (view["id"], min(start + DISABLE_GROUP, len(todo)), len(todo)))
    return {"disabled": disabled, "refused_by_phone": refused}


def _trim(adb: Adb, journal: Journal, sleep: Callable[[float], None]) -> Dict[str, Any]:
    before = read_space(adb, "/data")
    cmd = ["pm", "trim-caches", TRIM_TARGET]
    res = adb.shell(cmd, timeout=300, retry=False)
    sleep(2)
    after = read_space(adb, "/data")
    freed: Optional[int] = None
    if before and after:
        freed = max(0, after["free"] - before["free"])
    journal.record("trim_caches", ["shell"] + cmd, "ok" if res.code == 0 else "failed", freed=freed)
    out: Dict[str, Any] = {"freed_measured": freed, "command_ok": res.code == 0}
    if freed is None:
        out["note"] = "Free space could not be measured, so no claim is made about how much was cleared."
    return out


def run_apply(
    adb: Adb,
    plan_file: str,
    workdir: Optional[str] = None,
    confirm: Optional[str] = None,
    no_backup_batches: Sequence[str] = (),
    progress: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    say = progress or (lambda _m: None)
    serial = adb.require_serial()
    try:
        plan = load_plan(plan_file)
    except PlanError as exc:
        raise Refused("The plan file could not be used. Nothing was done.", exc.problems)
    batches, problems = check_plan(plan, serial)
    if problems:
        raise Refused("The plan breaks the safety rules. Nothing was done.", problems)
    code = approval_code(plan)
    info = device_info(adb)
    tools = probe_tools(adb)
    say("Checking the plan against the phone...")
    views, refusals = inspect_plan(adb, tools, info, batches)
    if refusals:
        raise Refused("The plan breaks the safety rules. Nothing was done.", refusals)

    unbacked = [v["id"] for v in views if v["kind"] == "delete_files" and not v["backup"]]
    backup_bytes = sum(v["size"] for v in views if v["kind"] == "delete_files" and v["backup"])
    ws = Workspace(workdir, serial)
    disk_free = free_disk(ws.base)
    data: Dict[str, Any] = {
        "plan": os.path.abspath(plan_file),
        "approval_code": code,
        "mode": "dry-run",
        "batches": views,
        "backup_space_needed": backup_bytes,
        "computer_free_space": disk_free,
        "delete_without_backup_batches": unbacked,
    }
    enough = disk_free >= backup_bytes * 1.05 + 200 * 1024 * 1024
    if backup_bytes and not enough:
        data["warning"] = "This computer has %s free but the backup copies need %s. Free up space on the computer first." % (
            human(disk_free), human(backup_bytes))

    if confirm is None:
        cmd = "python -m unbloat apply %s --confirm %s" % (plan_file, code)
        for b in unbacked:
            cmd += " --delete-without-backup %s" % b
        data["to_apply"] = cmd
        return _public(data)

    if confirm != code:
        raise Refused(
            "The approval code does not match this plan. Nothing was done.",
            ["Run the dry run again (python -m unbloat plan %s), show it to the user, and use the code it prints." % plan_file],
        )
    missing = [b for b in unbacked if b not in no_backup_batches]
    if missing:
        raise Refused(
            "Some batches delete files without keeping a copy. Nothing was done.",
            ["Batch %s has \"backup\": false. That needs the user's separate yes and the flag "
             "--delete-without-backup %s" % (b, b) for b in missing],
        )
    if backup_bytes and not enough:
        raise Refused("Not enough free space on this computer for the backup copies. Nothing was done.", [data["warning"]])

    journal = Journal(ws.journal_path, serial)
    snap = take_snapshot(adb, ws)
    journal.record("snapshot", None, "ok", path=snap["saved"])
    journal.record("plan_start", None, "ok", plan=data["plan"], approval_code=code)
    space_before = read_space(adb)
    folder = os.path.join(ws.dir("backups", "files"), stamp())
    data["mode"] = "applied"
    data["state_backup"] = snap["saved"]
    try:
        for view in views:
            say("Working on batch %s..." % view["id"])
            if view["kind"] == "delete_files":
                if view["backup"]:
                    os.makedirs(folder, exist_ok=True)
                view["result"] = _delete_batch(adb, tools, ws, journal, view, folder, say)
            elif view["kind"] == "trim_caches":
                view["result"] = _trim(adb, journal, sleep)
            else:
                view["result"] = _disable_batch(adb, journal, view, sleep, say)
                if view["result"].get("stopped"):
                    data["stopped"] = view["result"]["stopped"]
                    break
        space_after = read_space(adb)
    except AdbError as exc:
        journal.record("interrupted", None, "error", why=exc.message)
        raise Interrupted(
            "Stopped part-way: %s Everything done so far is recorded." % exc.message,
            "Reconnect and unlock the phone, then run the same apply command again to carry on. "
            "Files are only ever deleted after their copy is verified, so nothing is half-done.",
        )
    journal.record("plan_end", None, "ok", plan=data["plan"])
    data["free_before"] = space_before["free"] if space_before else None
    data["free_after"] = space_after["free"] if space_after else None
    if space_before and space_after:
        data["free_space_change_measured"] = space_after["free"] - space_before["free"]
    return _public(data)


def _public(data: Dict[str, Any]) -> Dict[str, Any]:
    for v in data["batches"]:
        v.pop("_ready", None)
    return data


def format_check(d: Dict[str, Any]) -> str:
    applied = d["mode"] == "applied"
    lines = ["RESULT" if applied else "DRY RUN - nothing has been changed", ""]
    for v in d["batches"]:
        lines.append("[%s] %s" % (v["id"], show(v["title"] or v["kind"])))
        r = v.get("result", {})
        if v["kind"] == "delete_files":
            if applied:
                lines.append("  deleted %d files, %s" % (r["deleted"], human(r["bytes_deleted"])))
                if r["backup_folder"]:
                    lines.append("  copies kept in %s" % r["backup_folder"])
                if r["failed_count"]:
                    lines.append("  %d files were NOT deleted:" % r["failed_count"])
                    lines.extend("    %s - %s" % (show(f["path"], 90), f["why"]) for f in r["failed"][:10])
                stale = r["media_index"].get("stale")
                if stale:
                    lines.append("  %d deleted files may still show in the gallery until the phone refreshes it" % stale)
            else:
                lines.append("  would delete %d files, %s" % (v["count"], human(v["size"])))
                lines.append("  backup copy first: %s" % ("yes" if v["backup"] else "NO - files would be gone for good"))
                if v["zones"]:
                    lines.append("  personal area: %s" % ", ".join(v["zones"]))
                for f in v["sample"]:
                    lines.append("    %10s  %s" % (human(f["size"]), show(f["path"], 100)))
                if v["count"] > len(v["sample"]):
                    lines.append("    ... and %d more" % (v["count"] - len(v["sample"])))
            if v.get("skipped_count"):
                lines.append("  %d files skipped (%s)" % (v["skipped_count"], v["skipped"][0]["why"]))
        elif v["kind"] == "trim_caches":
            if applied:
                if r["freed_measured"] is None:
                    lines.append("  cache clear ran; " + r["note"])
                else:
                    lines.append("  measured space freed: %s" % human(r["freed_measured"]))
            else:
                lines.append("  would ask Android to clear app caches (no logins or app data lost)")
        else:
            if applied:
                lines.append("  switched off: %s" % (", ".join(r["disabled"]) or "none"))
                if r["refused_by_phone"]:
                    lines.append("  the phone refused: %s" % ", ".join(r["refused_by_phone"]))
                if r.get("stopped"):
                    lines.append("  STOPPED: " + r["stopped"])
            else:
                for row in v["packages"]:
                    if row["action"] == "disable":
                        lines.append("  would switch off %s (%s, %s)" % (row["package"], row["name"] or "no description", row["tier"]))
                    else:
                        lines.append("  skip %s: %s" % (row["package"], row.get("why", "")))
        lines.append("")
    if d.get("warning"):
        lines.append("WARNING: " + d["warning"])
    if applied:
        if d.get("free_space_change_measured") is not None:
            lines.append("Free space on the phone, measured: %s before, %s after" % (human(d["free_before"]), human(d["free_after"])))
        lines.append("To undo app changes: python -m unbloat restore")
    else:
        lines.append("Approval code: %s" % d["approval_code"])
        lines.append("After the user says yes: " + d["to_apply"])
    return "\n".join(lines)
