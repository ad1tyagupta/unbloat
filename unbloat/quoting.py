"""Quoting for the phone's shell.

`adb shell <text>` hands <text> to a POSIX shell on the phone. File names on
the phone can contain anything, so every argument is wrapped in single quotes.
Inside single quotes the shell treats every character literally; the only
thing that cannot appear is a single quote, which is written as '\\''.
"""
from __future__ import annotations

from typing import Iterable


def sh_quote(arg: str) -> str:
    if not isinstance(arg, str):
        raise TypeError("shell arguments must be strings")
    if "\x00" in arg:
        raise ValueError("shell arguments cannot contain a NUL byte")
    return "'" + arg.replace("'", "'\\''") + "'"


def sh_join(args: Iterable[str]) -> str:
    return " ".join(sh_quote(a) for a in args)
