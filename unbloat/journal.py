"""Append-only record of everything unbloat did to a phone."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


class Journal:
    def __init__(self, path: str, serial: str):
        self.path = path
        self.serial = serial

    def record(
        self,
        action: str,
        command: Optional[List[str]] = None,
        result: str = "ok",
        **details: Any,
    ) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "serial": self.serial,
            "action": action,
            "command": command,
            "result": result,
        }
        entry.update(details)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        return entry

    def entries(self) -> List[Dict[str, Any]]:
        return read_journal(self.path)


def read_journal(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                # A half-written last line after a crash. Skip it, keep the rest.
                continue
    return out
