"""Small helpers shared across commands."""
from __future__ import annotations

from typing import Optional


def human(n: Optional[float]) -> str:
    """Bytes as a short readable size: 1536 -> '1.5 KB'."""
    if n is None:
        return "unknown"
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%s%d B" % (sign, n)
            return "%s%.1f %s" % (sign, n, unit)
        n /= 1024
    return "%s%.1f TB" % (sign, n)


def show(text: str, limit: int = 120) -> str:
    """Make phone-sourced text safe to print on one line.

    File names from the phone can hold newlines and control characters; shown
    raw they could fake extra lines of output. They are escaped here.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif code < 32 or code == 127 or 0xD800 <= code <= 0xDFFF:
            out.append("\\x%02x" % (code & 0xFF))
        else:
            out.append(ch)
    s = "".join(out)
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return s
