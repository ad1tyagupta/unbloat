"""`scan packages`: every app on the phone with what we know about it."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..adb import Adb
from ..device import device_info
from ..journal import read_journal
from ..restore import disabled_by_us
from ..workspace import Workspace
from .kb import lookup
from .state import list_packages, protected_by_defaults, read_defaults

TIER_ORDER = {"safe": 0, "caution": 1, "risky": 2, "unknown": 3, "never": 4}


def scan_packages(adb: Adb, workdir: Optional[str] = None) -> Dict[str, Any]:
    info = device_info(adb)
    family = str(info["family"])
    packages = list_packages(adb)
    defaults = read_defaults(adb)
    protected = protected_by_defaults(defaults)
    ws = Workspace.for_device(workdir, adb)
    ours = disabled_by_us(read_journal(ws.journal_path))
    rows: List[Dict[str, Any]] = []
    for name, st in sorted(packages.items()):
        know = lookup(name, family)
        tier = know["tier"]
        row: Dict[str, Any] = {
            "package": name,
            "name": know["name"],
            "system": st["system"],
            "enabled": st["enabled"],
            "installer": st["installer"],
            "tier": tier,
            "what": know["what"],
            "breaks": know["breaks"],
        }
        if name in protected and tier != "never":
            row["tier"] = "never"
            row["protected_now"] = protected[name]
        rows.append(row)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    candidates = [r for r in rows if r["enabled"] and r["tier"] in ("safe", "caution")]
    candidates.sort(key=lambda r: (TIER_ORDER[r["tier"]], r["package"]))
    data: Dict[str, Any] = {
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "device": {k: info[k] for k in ("serial", "brand", "model", "android", "sdk", "family", "skin")},
        "knowledge_base_family": family,
        "defaults": defaults,
        "counts": counts,
        "total": len(rows),
        "candidates": candidates,
        "switched_off_by_unbloat": [p for p in ours if p in packages and not packages[p]["enabled"]],
        "switched_back_on_since": [p for p in ours if p in packages and packages[p]["enabled"]],
        "packages": rows,
        "other_users": info["other_users"],
        "notes": list(info["notes"]),  # type: ignore[arg-type]
        "rules": "safe: may be proposed. caution: explain what breaks first. risky and unknown: only if the "
        "user asks about that exact app by name. never: cannot be switched off.",
    }
    if family == "unknown":
        data["notes"].append(
            "This brand has no knowledge-base file. Only the shared Google and carrier list applies; "
            "everything else is 'unknown' and will not be proposed."
        )
    data["saved"] = ws.write_json("scans", "packages", data)
    return data


def format_packages(d: Dict[str, Any]) -> str:
    dev = d["device"]
    lines = ["%s %s, Android %s: %d apps" % (dev["brand"], dev["model"], dev["android"], d["total"])]
    lines.append("  " + ", ".join("%s %d" % (t, d["counts"].get(t, 0)) for t in ("safe", "caution", "risky", "unknown", "never")))
    if d["candidates"]:
        lines.append("")
        lines.append("Could be switched off:")
        for r in d["candidates"]:
            lines.append("  [%s] %s - %s" % (r["tier"], r["package"], r["name"]))
            if r["tier"] == "caution":
                lines.append("        if off: %s" % r["breaks"])
    else:
        lines.append("Nothing on this phone matches the reviewed bloatware list.")
    if d["switched_off_by_unbloat"]:
        lines.append("")
        lines.append("Switched off by unbloat earlier: %s" % ", ".join(d["switched_off_by_unbloat"]))
    if d["switched_back_on_since"]:
        lines.append("Switched back on since then (often after a system update): %s" % ", ".join(d["switched_back_on_since"]))
    for n in d["notes"]:
        lines.append("Note: " + n)
    lines.append("")
    lines.append("Full list saved to %s" % d["saved"])
    return "\n".join(lines)
