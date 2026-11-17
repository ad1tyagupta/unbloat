"""Command line entry point: `python -m unbloat <command>`.

Every command returns a plain dict. With --json that dict is printed inside
an envelope the assistant can parse; otherwise a readable summary is printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .adb import Adb, AdbError
from .errors import Refused

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2


Handler = Callable[[argparse.Namespace, Adb], Tuple[Dict[str, Any], str]]


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--serial", help="which phone to use when several are connected")
    p.add_argument("--workdir", help="folder for scans, backups and the journal (default: ./unbloat-data)")
    p.add_argument("--adb", dest="adb_path", help="full path to the adb program")
    return p


def _cmd_doctor(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .doctor import format_doctor, run_doctor

    data = run_doctor(adb)
    return data, format_doctor(data)


def _cmd_connect(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .connect import format_connect, run_connect

    data = run_connect(adb, pair=args.pair, code=args.code, wireless=args.wireless)
    return data, format_connect(data)


def _cmd_scan(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    if args.what == "storage":
        from .storage.scan import format_storage, scan_storage

        data = scan_storage(
            adb,
            workdir=args.workdir,
            duplicates=not args.no_duplicates,
            unused_days=args.unused_days,
            progress=_progress(args),
        )
        return data, format_storage(data)
    from .packages.scan import format_packages, scan_packages

    data = scan_packages(adb, workdir=args.workdir)
    return data, format_packages(data)


def _cmd_backup(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .backup import format_backup, run_backup

    data = run_backup(adb, workdir=args.workdir)
    return data, format_backup(data)


def _cmd_plan(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .apply import format_check, run_apply

    data = run_apply(adb, args.plan_file, workdir=args.workdir, confirm=None, progress=_progress(args))
    return data, format_check(data)


def _cmd_apply(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .apply import format_check, run_apply

    data = run_apply(
        adb,
        args.plan_file,
        workdir=args.workdir,
        confirm=args.confirm,
        no_backup_batches=args.delete_without_backup or [],
        progress=_progress(args),
    )
    return data, format_check(data)


def _cmd_restore(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .restore import format_restore, run_restore

    data = run_restore(adb, workdir=args.workdir, dry_run=not args.confirm)
    return data, format_restore(data)


def _cmd_report(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .report import format_report, run_report

    data = run_report(adb, workdir=args.workdir)
    return data, format_report(data)


def _cmd_photos(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .photos import format_photos, run_photos

    data = run_photos(
        adb,
        workdir=args.workdir,
        folders=args.folder or None,
        limit=args.limit,
        consent=args.i_understand_images_go_to_my_ai_provider,
        progress=_progress(args),
    )
    return data, format_photos(data)


def _cmd_pick(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .pick import format_pick, run_pick

    data = run_pick(
        adb,
        args.group,
        args.plan,
        workdir=args.workdir,
        older_than_days=args.older_than_days,
        min_size_mb=args.min_size_mb,
        title=args.title,
        user_saw_sample=args.user_saw_sample,
        no_backup=args.no_backup,
    )
    return data, format_pick(data)


def _cmd_kb(args: argparse.Namespace, adb: Adb) -> Tuple[Dict[str, Any], str]:
    from .packages.kb import validate_all

    problems = validate_all()
    data = {"ok": not problems, "problems": problems}
    text = "Knowledge base is valid." if not problems else "\n".join(problems)
    if problems:
        raise Refused("The knowledge base has problems.", problems)
    return data, text


def _progress(args: argparse.Namespace) -> Callable[[str], None]:
    def show(message: str) -> None:
        try:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    return show


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="unbloat",
        description="Clean up an Android phone from your computer. Nothing changes on the phone "
        "unless you run 'apply' with the approval code from a dry run.",
    )
    parser.add_argument("--version", action="version", version="unbloat " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("doctor", parents=[common], help="check this computer is ready")
    p.set_defaults(handler=_cmd_doctor, needs_device=False)

    p = sub.add_parser("connect", parents=[common], help="connect a phone and describe it")
    p.add_argument("--pair", metavar="HOST:PORT", help="pair over Wi-Fi (Android 11+)")
    p.add_argument("--code", help="6-digit pairing code shown on the phone")
    p.add_argument("--wireless", metavar="HOST:PORT", help="connect over Wi-Fi after pairing")
    p.set_defaults(handler=_cmd_connect, needs_device=False)

    p = sub.add_parser("scan", parents=[common], help="look at the phone (read-only)")
    p.add_argument("what", choices=["storage", "packages"])
    p.add_argument("--no-duplicates", action="store_true", help="skip the duplicate-file search (faster)")
    p.add_argument("--unused-days", type=int, default=90, help="apps not opened for this long count as unused")
    p.set_defaults(handler=_cmd_scan)

    p = sub.add_parser("backup", parents=[common], help="save the phone's current settings and app states")
    p.set_defaults(handler=_cmd_backup)

    p = sub.add_parser("pick", parents=[common], help="add a group from the last scan to a plan file")
    p.add_argument("group", help="group id from 'scan storage' (e.g. installers, duplicates), or 'caches' or 'redisable'")
    p.add_argument("--plan", required=True, help="plan file to create or add to")
    p.add_argument("--older-than-days", type=int)
    p.add_argument("--min-size-mb", type=float)
    p.add_argument("--title")
    p.add_argument("--user-saw-sample", action="store_true", help="the user has seen count, size and a sample of these personal files")
    p.add_argument("--no-backup", action="store_true", help="mark the batch as delete-without-copy (needs a second yes at apply)")
    p.set_defaults(handler=_cmd_pick)

    p = sub.add_parser("plan", parents=[common], help="check a plan file and show what it would do")
    p.add_argument("plan_file")
    p.set_defaults(handler=_cmd_plan)

    p = sub.add_parser("apply", parents=[common], help="carry out an approved plan")
    p.add_argument("plan_file")
    p.add_argument("--confirm", metavar="CODE", help="approval code printed by the dry run")
    p.add_argument(
        "--delete-without-backup",
        metavar="BATCH_ID",
        action="append",
        help="allow this one batch to delete files with no copy kept (repeat per batch)",
    )
    p.set_defaults(handler=_cmd_apply)

    p = sub.add_parser("restore", parents=[common], help="switch back on everything unbloat switched off")
    p.add_argument("--confirm", action="store_true", help="actually do it (default is a preview)")
    p.set_defaults(handler=_cmd_restore)

    p = sub.add_parser("report", parents=[common], help="before/after summary")
    p.set_defaults(handler=_cmd_report)

    p = sub.add_parser("photos", parents=[common], help="opt-in photo review helper")
    p.add_argument("--folder", action="append", help="folder to review (default: DCIM and Pictures)")
    p.add_argument("--limit", type=int, default=2000, help="most photos to look at")
    p.add_argument(
        "--i-understand-images-go-to-my-ai-provider",
        action="store_true",
        help="needed before contact sheets are built for an assistant to look at",
    )
    p.set_defaults(handler=_cmd_photos)

    p = sub.add_parser("kb-check", parents=[common], help="validate the package knowledge base")
    p.set_defaults(handler=_cmd_kb, needs_device=False)
    return parser


def _emit(args: argparse.Namespace, payload: Dict[str, Any], text: str) -> None:
    if getattr(args, "json", False):
        out = json.dumps(payload, indent=1, ensure_ascii=True)
    else:
        out = text
    try:
        sys.stdout.write(out + "\n")
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        sys.stdout.write(out.encode(enc, "backslashreplace").decode(enc) + "\n")


def main(argv: Optional[List[str]] = None, adb: Optional[Adb] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    if adb is None:
        adb = Adb(adb_path=args.adb_path, serial=args.serial)
    elif args.serial:
        adb.serial = args.serial
    try:
        data, text = args.handler(args, adb)
    except Refused as exc:
        payload = {
            "ok": False,
            "command": args.command,
            "error": {"type": "refused", "message": exc.message, "reasons": exc.reasons},
        }
        text = "REFUSED: " + exc.message + "".join("\n  - " + r for r in exc.reasons)
        _emit(args, payload, text)
        return EXIT_REFUSED
    except AdbError as exc:
        payload = {
            "ok": False,
            "command": args.command,
            "error": {"type": exc.kind, "message": exc.message, "hint": exc.hint},
        }
        text = "Problem: " + exc.message + ("\n" + exc.hint if exc.hint else "")
        _emit(args, payload, text)
        return EXIT_ERROR
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped. Anything already done is in the journal; run 'report' to see it.\n")
        return EXIT_ERROR
    _emit(args, {"ok": True, "command": args.command, "data": data}, text)
    return EXIT_OK
