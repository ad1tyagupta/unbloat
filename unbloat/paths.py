"""Rules for which phone paths this tool may ever delete.

Everything here is pure string logic so it can be tested without a phone.
The on-device half (symlink and file-type checks) lives in `remote.py`.
"""
from __future__ import annotations

from typing import Optional

ROOT = "/storage/emulated/0"
_ALIASES = ("/sdcard", "/storage/self/primary", "/mnt/sdcard")

# App-owned areas inside shared storage. Never deleted from.
_PROTECTED = ("android/data", "android/obb")

# Folders where messaging apps keep media. Matched case-insensitively against
# the path below the shared-storage root.
MESSAGING_MARKERS = (
    "whatsapp/media",
    "whatsapp business/media",
    "android/media/com.whatsapp",
    "android/media/com.whatsapp.w4b",
    "telegram/",
    "android/media/org.telegram.messenger",
    "android/media/org.thunderdog.challegram",
    "pictures/telegram",
    "movies/telegram",
    "pictures/whatsapp",
    "pictures/messenger",
    "pictures/instagram",
    "movies/instagram",
    "movies/messenger",
    "signal/",
    "pictures/signal",
    "viber/",
    "android/media/com.viber.voip",
    "pictures/viber",
    "line/",
    "pictures/line",
    "snapchat/",
    "pictures/snapchat",
    "tencent/micromsg",
    "pictures/wechat",
    "kakaotalk/",
    "pictures/kakaotalk",
    "pictures/discord",
)

# Chat history backups that live in shared storage. Deleting them can lose
# years of messages if the user has no cloud backup, so they are never
# deleted by unbloat. Matched case-insensitively anywhere in the path.
_CHAT_BACKUPS = (
    "whatsapp/databases/",
    "whatsapp business/databases/",
    "whatsapp/backups/",
    "whatsapp business/backups/",
    "signal/backups/",
    "telegram/telegram files/backups/",
    "viber/backups/",
)

MAX_PATH_LEN = 4000


class PathRejected(ValueError):
    """A path the safety rules will not act on."""


def normalize_remote(path: object) -> str:
    """Return the canonical /storage/emulated/0/... form of `path`.

    Raises PathRejected for anything that is not plainly a path inside shared
    storage. Nothing is "fixed up": a path with `..` in it is refused, not
    resolved.
    """
    if not isinstance(path, str):
        raise PathRejected("path must be text")
    if not path:
        raise PathRejected("path is empty")
    if "\x00" in path:
        raise PathRejected("path contains a NUL byte")
    if len(path) > MAX_PATH_LEN:
        raise PathRejected("path is too long")
    if not path.startswith("/"):
        raise PathRejected("path must be absolute")
    for alias in _ALIASES:
        if path == alias or path.startswith(alias + "/"):
            path = ROOT + path[len(alias):]
            break
    if not path.startswith(ROOT + "/"):
        raise PathRejected("path is outside the phone's shared storage")
    rel = path[len(ROOT) + 1:]
    if not rel:
        raise PathRejected("path is the storage root itself")
    parts = rel.split("/")
    for part in parts:
        if part == "":
            raise PathRejected("path has an empty part (double or trailing slash)")
        if part in (".", ".."):
            raise PathRejected("path contains '.' or '..'")
    low = rel.casefold()
    for prot in _PROTECTED:
        if low == prot or low.startswith(prot + "/"):
            raise PathRejected("path is inside app-private storage (Android/data or Android/obb)")
    for marker in _CHAT_BACKUPS:
        if ("/" + low).find("/" + marker) != -1:
            raise PathRejected("path is a chat history backup (unbloat never deletes these)")
    return ROOT + "/" + rel


def relative(path: str) -> str:
    """Path below the shared-storage root. `path` must already be normalized."""
    return path[len(ROOT) + 1:]


def personal_zone(path: str) -> Optional[str]:
    """Name the personal-media zone a normalized path falls in, if any.

    Deleting from these zones needs the user to have seen counts, total size
    and a sample first.
    """
    low = relative(path).casefold()
    if low.startswith("dcim/"):
        return "DCIM"
    if low.startswith("pictures/"):
        for marker in MESSAGING_MARKERS:
            if low.startswith(marker):
                return "messaging media"
        return "Pictures"
    for marker in MESSAGING_MARKERS:
        if low.startswith(marker):
            return "messaging media"
    return None
