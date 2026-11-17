"""Packages that can never be switched off, whatever a plan says.

Android itself does not stop `pm disable-user` from disabling SystemUI or the
phone app (checked on an Android 14 emulator), so this list is the guard.
It errs on the side of refusing: a wrongly protected package costs nothing,
a wrongly disabled one can leave a phone unusable.
"""
from __future__ import annotations

import re
from typing import Optional

EXACT = {
    "android": "the Android system itself",
    "com.android.systemui": "the screen, status bar and navigation",
    "com.android.settings": "the Settings app",
    "com.android.phone": "phone calls and mobile network",
    "com.android.server.telecom": "phone call handling",
    "com.android.shell": "the connection unbloat itself uses",
    "com.android.vending": "the Play Store",
    "com.google.android.gms": "Google Play Services",
    "com.google.android.gsf": "Google Services Framework",
    "com.android.packageinstaller": "installing and removing apps",
    "com.google.android.packageinstaller": "installing and removing apps",
    "com.android.permissioncontroller": "app permissions",
    "com.google.android.permissioncontroller": "app permissions",
    "com.android.webview": "web pages inside apps",
    "com.google.android.webview": "web pages inside apps",
    "com.android.bluetooth": "Bluetooth",
    "com.google.android.bluetooth": "Bluetooth",
    "com.android.nfc": "NFC",
    "com.android.carrierconfig": "mobile network settings",
    "com.android.emergency": "emergency information",
    "com.android.externalstorage": "access to files",
    "com.android.documentsui": "the file picker",
    "com.google.android.documentsui": "the file picker",
    "com.android.keychain": "security certificates",
    "com.android.certinstaller": "security certificates",
    "com.android.location.fused": "location",
    "com.android.inputdevices": "keyboards and input",
    "com.android.se": "secure element (payments, SIM)",
    "com.android.mtp": "file transfer over USB",
    "com.android.vpndialogs": "VPN permission prompts",
    "com.android.localtransport": "backup",
    "com.android.mms.service": "sending and receiving MMS",
    "com.android.stk": "SIM card services",
    "com.android.cellbroadcastreceiver": "emergency alerts",
    "com.android.cellbroadcastservice": "emergency alerts",
    "com.google.android.cellbroadcastreceiver": "emergency alerts",
    "com.google.android.cellbroadcastservice": "emergency alerts",
    "com.android.ons": "mobile network selection",
    "com.android.dynsystem": "system updates",
    "com.google.android.ext.services": "core Android services",
    "com.android.ext.services": "core Android services",
    "com.google.android.ext.shared": "core Android services",
    "com.android.ext.shared": "core Android services",
    "com.sec.android.app.launcher": "Samsung home screen and recent apps",
    "com.samsung.android.incallui": "Samsung call screen",
    "com.samsung.android.dialer": "Samsung phone app",
    "com.samsung.android.messaging": "Samsung text messages",
    "com.sec.android.inputmethod": "Samsung keyboard",
    "com.samsung.android.honeyboard": "Samsung keyboard",
    "com.sec.phone": "Samsung phone service",
    "com.sec.imsservice": "calls over 4G and Wi-Fi",
    "com.sec.epdg": "Wi-Fi calling",
    "com.samsung.android.lool": "Samsung Device care (battery and storage management)",
    "com.miui.home": "Xiaomi home screen",
    "com.miui.securitycenter": "Xiaomi Security app (removing it stops the phone booting)",
    "com.miui.securitycore": "Xiaomi security core",
    "com.lbe.security.miui": "Xiaomi permission manager",
    "com.xiaomi.finddevice": "Xiaomi Find Device (removing it stops the phone booting)",
    "com.miui.core": "Xiaomi system core",
    "com.miui.system": "Xiaomi system core",
    "com.miui.rom": "Xiaomi system core",
    "com.miui.powerkeeper": "Xiaomi battery management",
    "com.xiaomi.xmsf": "Xiaomi service framework",
    "com.coloros.safecenter": "Oppo/realme phone manager and permissions",
    "com.oplus.safecenter": "Oppo/OnePlus phone manager and permissions",
    "com.oppo.launcher": "Oppo home screen",
    "com.oplus.launcher": "Oppo/OnePlus home screen",
    "net.oneplus.launcher": "OnePlus home screen",
    "com.bbk.launcher2": "Vivo home screen",
    "com.vivo.permissionmanager": "Vivo permission manager",
    "com.iqoo.secure": "Vivo phone manager and permissions",
    "com.motorola.launcher3": "Motorola home screen",
    "com.google.android.apps.nexuslauncher": "Pixel home screen and recent apps",
    "com.nothing.launcher": "Nothing home screen",
}

# (pattern, reason). Matched against the whole package name, lower-cased.
PATTERNS = tuple(
    (re.compile(p), why)
    for p, why in (
        (r"^com\.(google\.)?android\.providers\.", "a core data store (contacts, media, downloads, messages...)"),
        (r"^com\.samsung\.android\.providers\.", "a core Samsung data store"),
        (r"^com\.sec\.android\.providers?\.", "a core Samsung data store"),
        (r"(^|\.)telephony", "phone and mobile network"),
        (r"telecom", "phone call handling"),
        (r"(^|\.)ims(service)?($|\.)", "calls over 4G and Wi-Fi"),
        (r"\.imsservice", "calls over 4G and Wi-Fi"),
        (r"carrierconfig", "mobile network settings"),
        (r"emergency", "emergency calls and alerts"),
        (r"cellbroadcast", "emergency alerts"),
        (r"systemui", "the screen, status bar and navigation"),
        (r"packageinstaller", "installing and removing apps"),
        (r"permissioncontroller", "app permissions"),
        (r"webview", "web pages inside apps"),
        (r"\.bluetooth($|\.)", "Bluetooth"),
        (r"\.nfc($|\.)", "NFC"),
        (r"^com\.(google\.)?android\.networkstack", "network connections"),
        (r"^com\.(google\.)?android\.wifi", "Wi-Fi"),
        (r"^com\.(google\.)?android\.connectivity", "network connections"),
        (r"^com\.(google\.)?android\.tethering", "hotspot and network sharing"),
        (r"^com\.(google\.)?android\.modulemetadata", "system update bookkeeping"),
        (r"^com\.(google\.)?android\.captiveportallogin", "signing in to Wi-Fi networks"),
        (r"^com\.(google\.)?android\.overlay\.", "system appearance files"),
        (r"\.overlay($|\.)", "system appearance files"),
        (r"auto_generated_rro", "system appearance files"),
        (r"^com\.android\.internal\.", "system appearance files"),
        (r"^com\.android\.theme\.", "system appearance files"),
        (r"^com\.qualcomm\.qti\.(telephonyservice|ims)", "phone and mobile network"),
        (r"^org\.codeaurora\.ims", "calls over 4G and Wi-Fi"),
        (r"^com\.qualcomm\.qcrilmsgtunnel", "phone and mobile network"),
        (r"^com\.qti\.(phone|dpmserviceapp)", "phone and mobile network"),
        (r"^com\.mediatek\.ims", "calls over 4G and Wi-Fi"),
        (r"^com\.samsung\.android\.(app\.)?telephonyui", "Samsung phone screens"),
        (r"^com\.samsung\.(android\.)?networkstack", "network connections"),
        (r"^com\.samsung\.android\.wifi", "Wi-Fi"),
        (r"^com\.sec\.android\.app\.simsettingmgr", "SIM card settings"),
        (r"^com\.samsung\.android\.(knox\.|app\.knox|security\.)", "Samsung security"),
        (r"intentresolver", "the share and 'open with' menu"),
        (r"credentialmanager", "saved passwords and passkeys"),
        (r"managedprovisioning|setupwizard|\.setup($|\.)", "phone setup"),
        (r"^com\.google\.mainline\.", "Google system update component"),
        (
            r"^com\.google\.android\.(sdksandbox|adservices|federatedcompute|ondevicepersonalization|"
            r"healthconnect|health\.connect|uwb|rkpdapp|configupdater|devicelockcontroller|onetimeinitializer|"
            r"partnersetup|hotspot2|safetycenter|appsearch|art|os\.statsd|media($|\.)|mediaprovider|scheduling|"
            r"ipsec|resolv|conscrypt|adbd|tzdata|runtime|permission|neuralnetworks|virt|settings\.intelligence)",
            "Google system component",
        ),
        (r"\.resources$", "system resource files"),
        (r"keyguard|lockscreen|biometrics|fingerprint|faceunlock|facelock|trustagent", "screen lock and unlock"),
        (r"^com\.miui\.(securityadd|guardprovider|permission)", "Xiaomi security"),
        (r"\.simcontacts?($|\.)|simappdialog|simtoolkit|\.stk($|\.)", "SIM card services"),
    )
)

_NAME = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


def valid_package_name(name: object) -> bool:
    return isinstance(name, str) and 0 < len(name) <= 255 and bool(_NAME.match(name))


def core_android(package: str) -> bool:
    """Part of base Android. Never disabled unless the knowledge base has a
    reviewed entry saying otherwise."""
    return package.lower().startswith("com.android.")


def denied(package: str) -> Optional[str]:
    """Why this package may never be disabled, or None if the list allows it."""
    low = package.lower()
    for name, why in EXACT.items():
        if low == name:
            return why
    for pattern, why in PATTERNS:
        if pattern.search(low):
            return why
    return None
