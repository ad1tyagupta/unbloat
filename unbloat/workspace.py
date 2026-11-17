"""Where unbloat keeps its files on the computer (scans, backups, journal)."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, List, Optional

DEFAULT_DIRNAME = "unbloat-data"


def safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text).strip("._")
    return cleaned or "device"


def stamp(now: Optional[float] = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


class Workspace:
    def __init__(self, base: Optional[str], serial: str):
        base = base or os.environ.get("UNBLOAT_HOME") or os.path.join(os.getcwd(), DEFAULT_DIRNAME)
        self.base = os.path.abspath(base)
        self.serial = serial
        self.root = os.path.join(self.base, safe_name(serial))

    def dir(self, *parts: str) -> str:
        path = os.path.join(self.root, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def journal_path(self) -> str:
        return os.path.join(self.dir(), "journal.jsonl")

    def write_json(self, folder: str, prefix: str, data: Any) -> str:
        path = os.path.join(self.dir(folder), "%s-%s.json" % (prefix, stamp()))
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(self.dir(folder), "%s-%s-%d.json" % (prefix, stamp(), n))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=True)
        os.replace(tmp, path)
        return path

    def list(self, folder: str, prefix: str) -> List[str]:
        d = os.path.join(self.root, folder)
        if not os.path.isdir(d):
            return []
        names = sorted(n for n in os.listdir(d) if n.startswith(prefix + "-") and n.endswith(".json"))
        return [os.path.join(d, n) for n in names]

    def latest(self, folder: str, prefix: str) -> Optional[str]:
        items = self.list(folder, prefix)
        return items[-1] if items else None

    def first(self, folder: str, prefix: str) -> Optional[str]:
        items = self.list(folder, prefix)
        return items[0] if items else None


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
