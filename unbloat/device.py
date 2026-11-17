"""Facts about the connected phone: model, Android version, users, shell tools."""
from __future__ import annotations

import re
from typing import Dict, List

from .adb import Adb

_PROP_LINE = re.compile(r"^\[([^\]]+)\]: \[(.*)\]\s*$")

_SKIN_PROPS = (
    ("One UI", "ro.build.version.oneui"),
    ("HyperOS", "ro.mi.os.version.name"),
    ("MIUI", "ro.miui.ui.version.name"),
    ("ColorOS", "ro.build.version.oplusrom"),
    ("ColorOS", "ro.build.version.opporom"),
    ("realme UI", "ro.build.version.realmeui"),
    ("OxygenOS", "ro.oxygen.version"),
    ("Vivo OS", "ro.vivo.os.version"),
    ("Nothing OS", "ro.nothing.version.id"),
)

_FAMILIES = (
    ("samsung", ("samsung",)),
    ("xiaomi", ("xiaomi", "redmi", "poco")),
    ("oppo", ("oppo", "realme", "oneplus")),
    ("vivo", ("vivo", "iqoo")),
    ("motorola", ("motorola", "moto")),
    ("pixel", ("google",)),
    ("nothing", ("nothing",)),
)


def parse_getprop(text: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for line in text.splitlines():
        m = _PROP_LINE.match(line.rstrip("\r"))
        if m:
            props[m.group(1)] = m.group(2)
    return props


_USER_LINE = re.compile(r"UserInfo\{(\d+):(.*):([0-9a-fA-F]+)\}(.*)$")


def parse_users(text: str) -> List[Dict[str, object]]:
    users: List[Dict[str, object]] = []
    for line in text.splitlines():
        m = _USER_LINE.search(line)
        if not m:
            continue
        try:
            flags = int(m.group(3), 16)
        except ValueError:
            flags = 0
        users.append(
            {
                "id": int(m.group(1)),
                "name": m.group(2),
                "running": "running" in m.group(4),
                # FLAG_MANAGED_PROFILE = 0x20 in android.content.pm.UserInfo
                "work_profile": bool(flags & 0x20),
            }
        )
    return users


def brand_family(brand: str, manufacturer: str) -> str:
    blob = (brand + " " + manufacturer).lower()
    for family, names in _FAMILIES:
        if any(n in blob for n in names):
            return family
    return "unknown"


def device_info(adb: Adb) -> Dict[str, object]:
    props = parse_getprop(adb.shell_text(["getprop"]))
    brand = props.get("ro.product.brand", "")
    manufacturer = props.get("ro.product.manufacturer", "")
    sdk_text = props.get("ro.build.version.sdk", "")
    skin = None
    for name, key in _SKIN_PROPS:
        if props.get(key):
            skin = {"name": name, "version": props[key]}
            break
    users = parse_users(adb.shell_text(["pm", "list", "users"]))
    others = [u for u in users if u["id"] != 0]
    info: Dict[str, object] = {
        "serial": adb.require_serial(),
        "brand": brand or "unknown",
        "manufacturer": manufacturer or "unknown",
        "model": props.get("ro.product.model", "unknown"),
        "android": props.get("ro.build.version.release", "unknown"),
        "sdk": int(sdk_text) if sdk_text.isdigit() else None,
        "security_patch": props.get("ro.build.version.security_patch") or None,
        "skin": skin,
        "family": brand_family(brand, manufacturer),
        "users": users,
        "other_users": others,
    }
    notes: List[str] = []
    sdk = info["sdk"]
    if sdk is None:
        notes.append("Could not read the Android version.")
    elif sdk < 26:
        notes.append("This phone runs Android older than 8. unbloat is not built for it.")
    if others:
        notes.append(
            "This phone has %d extra user(s) or a work profile. unbloat only works on the "
            "main user and leaves the others alone." % len(others)
        )
    info["notes"] = notes
    return info


_PROBE = (
    "for t in find stat du df sha256sum md5sum realpath readlink rm ls; do "
    'if command -v "$t" >/dev/null 2>&1; then echo "$t"; fi; done'
)


def probe_tools(adb: Adb) -> Dict[str, bool]:
    """Find out which shell tools this phone has. Read-only."""
    have = set(adb.shell_script(_PROBE).text.split())
    tools = {
        name: name in have
        for name in ("find", "stat", "du", "df", "sha256sum", "md5sum", "realpath", "readlink", "rm", "ls")
    }
    tools["find_printf"] = False
    tools["stat_c"] = False
    if tools["find"]:
        res = adb.shell(["find", "/system/bin/sh", "-maxdepth", "0", "-printf", "%s\\t%T@\\0"])
        tools["find_printf"] = res.code == 0 and bool(re.match(rb"^\d+\t\d+(\.\d+)?\x00$", res.stdout))
    if tools["stat"]:
        res = adb.shell(["stat", "-c", "%F|%s|%Y", "/system/bin/sh"])
        tools["stat_c"] = res.code == 0 and bool(re.match(r"^[a-z ]+\|\d+\|\d+\s*$", res.text))
    return tools
