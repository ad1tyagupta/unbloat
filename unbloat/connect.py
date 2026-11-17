"""`connect`: get a phone talking to this computer, then describe it."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .adb import Adb, AdbError
from .device import device_info

USB_STEPS = [
    "On the phone open Settings > About phone.",
    "Tap 'Build number' seven times (on Samsung: About phone > Software information; "
    "on Xiaomi tap 'MIUI version' or 'OS version').",
    "Go back to Settings, open 'Developer options' (sometimes under System or Additional settings).",
    "Turn on 'USB debugging'.",
    "Plug the phone into this computer with a data cable and unlock it.",
    "Tap Allow on the 'Allow USB debugging?' box. Tick 'Always allow from this computer'.",
    "Run this command again.",
]

WIRELESS_STEPS = [
    "Needs Android 11 or newer, and phone and computer on the same Wi-Fi.",
    "On the phone: Settings > Developer options > Wireless debugging > turn it on.",
    "Tap 'Pair device with pairing code'. The phone shows an address like 192.168.1.5:37121 and a 6-digit code.",
    "Run: python -m unbloat connect --pair <address:port> --code <6 digits>",
    "Then look at the main Wireless debugging screen for 'IP address & Port' (a different port).",
    "Run: python -m unbloat connect --wireless <address:port>",
]

STATE_HELP = {
    "unauthorized": "Unlock the phone and tap Allow on the 'Allow USB debugging?' box. "
    "If no box appears: Developer options > Revoke USB debugging authorisations, then replug.",
    "offline": "Unplug and replug the cable, and unlock the phone.",
    "no permissions": "Linux: your user may not access USB devices. Install the 'android-udev-rules' "
    "package (or add yourself to the 'plugdev' group), then replug.",
    "authorizing": "The phone is still deciding. Unlock it and look for the Allow box.",
}


def run_connect(
    adb: Adb,
    pair: Optional[str] = None,
    code: Optional[str] = None,
    wireless: Optional[str] = None,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {"connected": False, "devices": [], "steps": [], "messages": []}
    if pair:
        if not code:
            raise AdbError("Pairing needs the 6-digit code shown on the phone.", "Add --code 123456")
        res = adb.host(["pair", pair, code], timeout=40)
        text = (res.text + res.err).strip()
        ok = res.code == 0 and "success" in text.lower()
        data["messages"].append("Pairing %s." % ("worked" if ok else "failed: " + text))
        if not ok:
            data["steps"] = WIRELESS_STEPS
            return data
    if wireless:
        res = adb.host(["connect", wireless], timeout=30)
        text = (res.text + res.err).strip()
        ok = "connected to" in text.lower()
        data["messages"].append("Wi-Fi connection %s." % ("worked" if ok else "failed: " + text))
        if not ok:
            data["steps"] = WIRELESS_STEPS
            return data
        if not adb.serial:
            adb.serial = wireless
    devices = adb.devices()
    data["devices"] = devices
    if not devices:
        data["messages"].append("No phone found.")
        data["steps"] = USB_STEPS
        data["wireless_steps"] = WIRELESS_STEPS
        return data
    ready = [d for d in devices if d["state"] == "device"]
    if adb.serial:
        chosen = [d for d in devices if d["serial"] == adb.serial]
        if not chosen:
            data["messages"].append("The phone %s is not connected." % adb.serial)
            return data
        target = chosen[0]
    elif len(devices) > 1:
        data["messages"].append(
            "More than one phone is connected. Choose one and add --serial <serial> to every command."
        )
        data["needs_serial"] = True
        return data
    else:
        target = devices[0]
    if target["state"] != "device":
        data["messages"].append("The phone is connected but not ready (state: %s)." % target["state"])
        hint = STATE_HELP.get(target["state"])
        data["steps"] = [hint] if hint else USB_STEPS
        return data
    adb.serial = target["serial"]
    data["device"] = device_info(adb)
    data["connected"] = True
    data["ready_count"] = len(ready)
    return data


def format_connect(d: Dict[str, Any]) -> str:
    lines: List[str] = list(d.get("messages", []))
    if d.get("needs_serial"):
        for dev in d["devices"]:
            lines.append("  %s  %s  %s" % (dev["serial"], dev["state"], dev.get("model", "")))
    if d.get("connected"):
        dev = d["device"]
        skin = dev.get("skin")
        lines.append("Connected: %s %s" % (dev["brand"], dev["model"]))
        lines.append(
            "Android %s (SDK %s)%s"
            % (dev["android"], dev["sdk"], ", %s %s" % (skin["name"], skin["version"]) if skin else "")
        )
        lines.append("Serial: %s" % dev["serial"])
        for u in dev["users"]:
            lines.append(
                "User %s: %s%s" % (u["id"], u["name"], " (work profile)" if u["work_profile"] else "")
            )
        lines.extend(dev.get("notes", []))
    if d.get("steps"):
        lines.append("")
        lines.append("What to do:")
        lines.extend("  %d. %s" % (i + 1, s) for i, s in enumerate(d["steps"]))
    if d.get("wireless_steps"):
        lines.append("")
        lines.append("Or connect over Wi-Fi:")
        lines.extend("  - " + s for s in d["wireless_steps"])
    return "\n".join(lines)
