"""Tolerant parsers for phone command output.

`dumpsys` and `df` are not stable interfaces. Every parser here returns None
(or an empty result) when it does not recognise the text, never raises.
"""
from __future__ import annotations

import json
import re
import time
from typing import Dict, Optional

_UNITS = {"": 1, "B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def parse_statfs(text: str) -> Optional[Dict[str, int]]:
    """Output of `stat -f -c '%S %b %f %a' <path>`."""
    parts = text.split()
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return None
    bsize, blocks, free, avail = (int(p) for p in parts)
    if bsize <= 0 or blocks <= 0:
        return None
    total = bsize * blocks
    return {"total": total, "free": bsize * avail, "used": total - bsize * free}


def _unit_number(token: str) -> Optional[int]:
    m = re.match(r"^(\d+(?:\.\d+)?)([KMGT]?)i?B?$", token, re.I)
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def parse_df(text: str) -> Optional[Dict[str, int]]:
    """Understands toybox `df -k` and the older toolbox `df` table."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    header = lines[0].split()
    row = lines[-1].split()
    low = [h.lower() for h in header]
    try:
        if "1k-blocks" in low and len(row) >= 4:
            i = low.index("1k-blocks")
            total, used, avail = (int(row[i]) * 1024, int(row[i + 1]) * 1024, int(row[i + 2]) * 1024)
            return {"total": total, "used": used, "free": avail}
        if "size" in low and "used" in low and len(row) >= 4:
            total = _unit_number(row[low.index("size")])
            used = _unit_number(row[low.index("used")])
            free_col = "free" if "free" in low else ("avail" if "avail" in low else None)
            free = _unit_number(row[low.index(free_col)]) if free_col else None
            if total is None or used is None:
                return None
            return {"total": total, "used": used, "free": free if free is not None else total - used}
    except (ValueError, IndexError):
        return None
    return None


def _json_list(text: str, label: str) -> Optional[list]:
    m = re.search(r"^%s:\s*(\[.*\])\s*$" % re.escape(label), text, re.M)
    if not m:
        return None
    try:
        value = json.loads(m.group(1))
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def parse_diskstats(text: str) -> Optional[Dict[str, Dict[str, Optional[int]]]]:
    """Per-app sizes from `dumpsys diskstats`: package -> app/data/cache bytes.

    Returns None when the phone has not computed them (it does so about once
    a day while idle and charging) or the format is not recognised.
    """
    names = _json_list(text, "Package Names")
    app = _json_list(text, "App Sizes")
    data = _json_list(text, "App Data Sizes")  # absent on Android 8
    cache = _json_list(text, "Cache Sizes")
    if not names or app is None or cache is None:
        return None
    if data is None and re.search(r"^App Data Sizes:", text, re.M):
        return None  # the line is there but unreadable
    if not (len(names) == len(app) == len(cache)) or (data is not None and len(data) != len(names)):
        return None
    out: Dict[str, Dict[str, Optional[int]]] = {}
    for i, n in enumerate(names):
        if not isinstance(n, str):
            return None
        try:
            out[n] = {"app": int(app[i]), "data": int(data[i]) if data is not None else None, "cache": int(cache[i])}
        except (TypeError, ValueError):
            return None
    return out


_USAGE = re.compile(r'package=(\S+)\s.*?lastTime(?:Used)?="([^"]*)"')
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y, %I:%M %p", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S")


def parse_device_time(text: str) -> Optional[float]:
    """A wall-clock time printed by the phone, as seconds on a naive timeline.

    The phone's time zone is unknown to us, so these numbers are only ever
    compared with other times from the same phone.
    """
    text = text.strip()
    for fmt in _TIME_FORMATS:
        try:
            return float(time.mktime(time.strptime(text, fmt)))
        except (ValueError, OverflowError):
            continue
    return None


def parse_usagestats(text: str) -> Dict[str, Optional[float]]:
    """Last time each app was opened, from `dumpsys usagestats`.

    package -> naive seconds, 0.0 for "not opened in the period the phone
    remembers", None when the date could not be read.
    """
    out: Dict[str, Optional[float]] = {}
    for m in _USAGE.finditer(text):
        pkg, when = m.group(1), m.group(2)
        ts = parse_device_time(when)
        if ts is not None and when[:4].isdigit() and int(when[:4]) < 2005:
            ts = 0.0
        elif ts is not None and ts < 1.1e9:
            ts = 0.0
        prev = out.get(pkg, -1.0)
        if ts is None:
            if pkg not in out:
                out[pkg] = None
        elif prev is None or ts > prev:
            out[pkg] = ts
    return out
