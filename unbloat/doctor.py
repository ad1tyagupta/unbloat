"""`doctor`: is this computer ready?"""
from __future__ import annotations

import os
import platform
import sys
from typing import Any, Dict

from .adb import Adb, AdbError, find_adb

INSTALL_HELP = {
    "Darwin": [
        "If you have Homebrew:  brew install android-platform-tools",
        "Otherwise download 'SDK Platform-Tools for Mac' from "
        "https://developer.android.com/tools/releases/platform-tools , unzip it, "
        "and move the 'platform-tools' folder into this project folder.",
    ],
    "Linux": [
        "Debian/Ubuntu:  sudo apt install adb",
        "Fedora:  sudo dnf install android-tools",
        "Arch:  sudo pacman -S android-tools",
        "Or download 'SDK Platform-Tools for Linux' from "
        "https://developer.android.com/tools/releases/platform-tools , unzip it, "
        "and move the 'platform-tools' folder into this project folder.",
    ],
    "Windows": [
        "Download 'SDK Platform-Tools for Windows' from "
        "https://developer.android.com/tools/releases/platform-tools , unzip it, "
        "and move the 'platform-tools' folder into this project folder.",
        "Or with winget:  winget install Google.PlatformTools",
        "If the phone never shows up, install your phone maker's USB driver.",
    ],
}


def run_doctor(adb: Adb) -> Dict[str, Any]:
    system = platform.system() or "Unknown"
    py_ok = sys.version_info >= (3, 9)
    data: Dict[str, Any] = {
        "python": {"version": platform.python_version(), "ok": py_ok},
        "os": system,
        "adb": {"found": False, "path": None, "version": None},
        "pillow": False,
        "problems": [],
        "next_steps": [],
    }
    if not py_ok:
        data["problems"].append("Python 3.9 or newer is needed. This is %s." % platform.python_version())
        data["next_steps"].append("Install a newer Python from https://www.python.org/downloads/")
    try:
        import PIL  # noqa: F401

        data["pillow"] = True
    except Exception:
        data["pillow"] = False
    path = None
    try:
        path = adb.path
    except AdbError:
        path = find_adb()
    if not path:
        data["problems"].append("adb (Android platform-tools) is not installed.")
        data["next_steps"].extend(INSTALL_HELP.get(system, INSTALL_HELP["Linux"]))
    else:
        data["adb"]["found"] = True
        data["adb"]["path"] = path
        try:
            res = adb.host(["version"], timeout=20)
            for line in res.text.splitlines():
                if line.lower().startswith("version"):
                    data["adb"]["version"] = line.split(None, 1)[1].strip()
                    break
            if data["adb"]["version"] is None and res.text.strip():
                data["adb"]["version"] = res.text.strip().splitlines()[0]
        except AdbError as exc:
            data["problems"].append("adb was found but did not run: %s" % exc.message)
    data["ok"] = not data["problems"]
    if data["ok"]:
        data["next_steps"].append("Connect your phone: python -m unbloat connect")
    return data


def format_doctor(d: Dict[str, Any]) -> str:
    lines = [
        "Python %s  %s" % (d["python"]["version"], "ok" if d["python"]["ok"] else "TOO OLD"),
        "System  %s" % d["os"],
    ]
    if d["adb"]["found"]:
        lines.append("adb     %s (%s)" % (d["adb"]["version"] or "unknown version", d["adb"]["path"]))
    else:
        lines.append("adb     NOT FOUND")
    lines.append("Pillow  %s" % ("installed" if d["pillow"] else "not installed (only needed for photo review)"))
    if d["problems"]:
        lines.append("")
        lines.append("Problems:")
        lines.extend("  - " + p for p in d["problems"])
    if d["next_steps"]:
        lines.append("")
        lines.append("Next:")
        lines.extend("  - " + p for p in d["next_steps"])
    return os.linesep.join(lines)
