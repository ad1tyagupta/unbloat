"""Read the phone's package list and its current default apps. Read-only."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..adb import Adb, AdbError


def _names(text: str) -> List[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        token = line[len("package:"):].split()[0] if line[len("package:"):].split() else ""
        if "=" in token:
            token = token.rsplit("=", 1)[1]
        if token:
            out.append(token)
    return out


def parse_installers(text: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^package:(\S+)\s+installer=(\S+)", line.strip())
        if m:
            out[m.group(1)] = None if m.group(2) == "null" else m.group(2)
    return out


def list_packages(adb: Adb) -> Dict[str, Dict[str, object]]:
    """package -> {system, enabled, installer} for the main user."""
    all_names = _names(adb.shell_text(["pm", "list", "packages", "--user", "0"], timeout=90))
    if not all_names:
        raise AdbError("The phone did not return its app list.", "Unlock the phone and try again.")
    system = set(_names(adb.shell_text(["pm", "list", "packages", "-s", "--user", "0"], timeout=90)))
    disabled = set(_names(adb.shell_text(["pm", "list", "packages", "-d", "--user", "0"], timeout=90)))
    installers = parse_installers(adb.shell_text(["pm", "list", "packages", "-i", "--user", "0"], timeout=90))
    return {
        name: {"system": name in system, "enabled": name not in disabled, "installer": installers.get(name)}
        for name in all_names
    }


def disabled_packages(adb: Adb) -> set:
    return set(_names(adb.shell_text(["pm", "list", "packages", "-d", "--user", "0"], timeout=90)))


def _component_pkg(text: str) -> Optional[str]:
    text = text.strip()
    if not text or text == "null" or " " in text:
        return None
    pkg = text.split("/", 1)[0].strip()
    return pkg if re.match(r"^[A-Za-z0-9_.]+$", pkg) and "." in pkg else None


def parse_resolve_activity(text: str) -> Optional[str]:
    """Package from `cmd package resolve-activity --brief` (last component line)."""
    found = None
    for line in text.splitlines():
        line = line.strip()
        if "/" in line and " " not in line:
            found = _component_pkg(line)
    if found in (None, "android", "com.android.internal.app"):
        return None
    return found


def parse_query_activities(text: str) -> List[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if "/" in line and " " not in line:
            pkg = _component_pkg(line)
            if pkg and pkg not in out:
                out.append(pkg)
    return out


def parse_role_holders(text: str) -> List[str]:
    out = []
    for token in re.split(r"[\s;,]+", text.strip()):
        if re.match(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$", token):
            out.append(token)
    return out


_COMPONENT = re.compile(r"ComponentInfo\{([A-Za-z0-9_.]+)/")
_ADMIN_LINE = re.compile(r"^\s+([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+:\s*$")


def parse_device_admins(text: str) -> List[str]:
    """Packages that are active device admins, from `dumpsys device_policy`.

    Deliberately greedy: any component named in the dump counts."""
    out: List[str] = []
    for m in _COMPONENT.finditer(text):
        if m.group(1) not in out:
            out.append(m.group(1))
    for line in text.splitlines():
        m = _ADMIN_LINE.match(line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def parse_dumpsys_role(text: str, role: str) -> List[str]:
    """Holders of one role from `dumpsys role` (Android 10, which has roles
    but no `cmd role get-role-holders`)."""
    m = re.search(r"name=%s\s*\n\s*holders=([^\n]*)" % re.escape(role), text)
    return parse_role_holders(m.group(1)) if m else []


def _role(adb: Adb, role: str, dump: List[str]) -> List[str]:
    holders = parse_role_holders_strict(adb.shell_text(["cmd", "role", "get-role-holders", role]))
    if holders:
        return holders
    if not dump:
        dump.append(adb.shell_text(["dumpsys", "role"], timeout=60))
    return parse_dumpsys_role(dump[0], role)


def parse_role_holders_strict(text: str) -> List[str]:
    """Like parse_role_holders, but ignores error text such as
    'Unknown command: get-role-holders'."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) != 1:
        return []
    if any(word in lines[0].lower() for word in ("unknown", "can't", "error", "exception", "usage")):
        return []
    return parse_role_holders(lines[0])


def _setting(adb: Adb, namespace: str, key: str) -> str:
    return adb.shell_text(["settings", "get", namespace, key]).strip()


def read_defaults(adb: Adb) -> Dict[str, object]:
    """The apps the phone currently relies on. Resolved fresh every time."""
    home = ["-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"]
    launcher = parse_resolve_activity(adb.shell_text(["cmd", "package", "resolve-activity", "--brief"] + home))
    launchers = parse_query_activities(adb.shell_text(["cmd", "package", "query-activities", "--brief"] + home))
    dump: List[str] = []
    if launcher is None:
        holders = _role(adb, "android.app.role.HOME", dump)
        launcher = holders[0] if holders else None
    keyboard = _component_pkg(_setting(adb, "secure", "default_input_method"))
    dialers = _role(adb, "android.app.role.DIALER", dump)
    if not dialers:
        d = _component_pkg(_setting(adb, "secure", "dialer_default_application"))
        if not d:
            lines = adb.shell_text(["telecom", "get-default-dialer"]).strip().splitlines()
            d = _component_pkg(lines[-1]) if lines else None
        dialers = [d] if d else []
    sms = _role(adb, "android.app.role.SMS", dump)
    if not sms:
        s = _component_pkg(_setting(adb, "secure", "sms_default_application"))
        sms = [s] if s else []
    acc_raw = _setting(adb, "secure", "enabled_accessibility_services")
    accessibility = []
    for item in acc_raw.split(":"):
        pkg = _component_pkg(item)
        if pkg and pkg not in accessibility:
            accessibility.append(pkg)
    admins = parse_device_admins(adb.shell_text(["dumpsys", "device_policy"], timeout=60))
    return {
        "launcher": launcher,
        "launcher_candidates": launchers,
        "keyboard": keyboard,
        "dialer": dialers,
        "sms": sms,
        "accessibility": accessibility,
        "device_admins": admins,
    }


def protected_by_defaults(defaults: Dict[str, object]) -> Dict[str, str]:
    """package -> why it is protected right now on this phone."""
    out: Dict[str, str] = {}
    if defaults.get("launcher"):
        out[str(defaults["launcher"])] = "it is your current home screen"
    else:
        for p in defaults.get("launcher_candidates") or []:  # type: ignore[union-attr]
            out[str(p)] = "it may be your home screen (the phone did not say which one is the default)"
    if defaults.get("keyboard"):
        out.setdefault(str(defaults["keyboard"]), "it is your current keyboard")
    for p in defaults.get("dialer") or []:  # type: ignore[union-attr]
        out.setdefault(str(p), "it is your current phone app")
    for p in defaults.get("sms") or []:  # type: ignore[union-attr]
        out.setdefault(str(p), "it is your current text-message app")
    for p in defaults.get("accessibility") or []:  # type: ignore[union-attr]
        out.setdefault(str(p), "it provides an accessibility service you have switched on")
    for p in defaults.get("device_admins") or []:  # type: ignore[union-attr]
        out.setdefault(str(p), "it is a device administrator")
    return out


def essentials_missing(defaults: Dict[str, object]) -> List[str]:
    """Things we must know before disabling anything. Empty list means go."""
    missing = []
    if not defaults.get("launcher") and not defaults.get("launcher_candidates"):
        missing.append("home screen app")
    if not defaults.get("keyboard"):
        missing.append("keyboard app")
    return missing
