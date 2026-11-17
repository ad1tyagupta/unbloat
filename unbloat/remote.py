"""Operations on exact file paths on the phone: stat, hash, resolve, delete.

Every function takes paths that were already normalized by `paths.py` and
passes them to the phone as quoted arguments.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence

from .adb import Adb
from .paths import ROOT, PathRejected

# Keep each shell command short. Old phones and old adb versions have small
# limits on command length.
CHUNK_BYTES = 3000

_STAT_LINE = re.compile(r"^([a-z ]+)\|(\d+)\|(\d+)$")


def chunked(paths: Sequence[str], budget: int = CHUNK_BYTES) -> List[List[str]]:
    chunks: List[List[str]] = []
    cur: List[str] = []
    used = 0
    for p in paths:
        cost = len(p.encode("utf-8", "surrogateescape")) + 4 + 3 * p.count("'")
        if cur and used + cost > budget:
            chunks.append(cur)
            cur, used = [], 0
        cur.append(p)
        used += cost
    if cur:
        chunks.append(cur)
    return chunks


def _parse_stat(line: str) -> Optional[Dict[str, object]]:
    m = _STAT_LINE.match(line.strip())
    if not m:
        return None
    return {"type": m.group(1), "size": int(m.group(2)), "mtime": int(m.group(3))}


def stat_one(adb: Adb, path: str) -> Optional[Dict[str, object]]:
    """Type, size and modified time of one path. None if it does not exist."""
    res = adb.shell(["stat", "-c", "%F|%s|%Y", path])
    if res.code != 0:
        return None
    return _parse_stat(res.text)


def stat_many(adb: Adb, paths: Sequence[str]) -> List[Optional[Dict[str, object]]]:
    out: List[Optional[Dict[str, object]]] = []
    for chunk in chunked(paths):
        res = adb.shell(["stat", "-c", "%F|%s|%Y"] + chunk, timeout=120)
        lines = res.text.splitlines()
        parsed = [_parse_stat(l) for l in lines]
        if res.code == 0 and len(parsed) == len(chunk) and all(parsed):
            out.extend(parsed)
        else:
            out.extend(stat_one(adb, p) for p in chunk)
    return out


def pick_hash_tool(tools: Dict[str, bool]) -> Optional[str]:
    if tools.get("sha256sum"):
        return "sha256sum"
    if tools.get("md5sum"):
        return "md5sum"
    return None


_HEX = {"sha256sum": 64, "md5sum": 32}


def _hash_line(tool: str, line: str) -> Optional[str]:
    n = _HEX[tool]
    line = line.lstrip("\\")
    if len(line) >= n and re.match(r"^[0-9a-f]{%d}(\s|$)" % n, line):
        return line[:n]
    return None


def hash_one(adb: Adb, tool: str, path: str, timeout: float = 900) -> Optional[str]:
    res = adb.shell([tool, path], timeout=timeout)
    if res.code != 0:
        return None
    first = res.text.split("\n", 1)[0]
    return _hash_line(tool, first)


def hash_many(
    adb: Adb,
    tool: str,
    paths: Sequence[str],
    sizes: Optional[Dict[str, int]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Optional[str]]:
    """Hash files on the phone. Returns path -> hex digest (None if it failed)."""
    out: Dict[str, Optional[str]] = {}
    def is_tricky(p: str) -> bool:
        return "\n" in p or "\\" in p or "\r" in p

    simple = [p for p in paths if not is_tricky(p)]
    tricky = [p for p in paths if is_tricky(p)]
    done = 0
    total = len(paths)
    for chunk in chunked(simple):
        size = sum((sizes or {}).get(p, 0) for p in chunk)
        timeout = 120 + size / (2 * 1024 * 1024)
        res = adb.shell([tool] + chunk, timeout=timeout)
        got = [_hash_line(tool, l) for l in res.text.splitlines()]
        if res.code == 0 and len(got) == len(chunk) and all(got):
            for p, h in zip(chunk, got):
                out[p] = h
        else:
            for p in chunk:
                out[p] = hash_one(adb, tool, p)
        done += len(chunk)
        if progress:
            progress(done, total)
    for p in tricky:
        out[p] = hash_one(adb, tool, p)
        done += 1
        if progress:
            progress(done, total)
    return out


def canonical(adb: Adb, tools: Dict[str, bool], path: str) -> Optional[str]:
    """Where the phone says `path` really points, following all symlinks."""
    if tools.get("realpath"):
        res = adb.shell(["realpath", path])
    elif tools.get("readlink"):
        res = adb.shell(["readlink", "-f", path])
    else:
        return None
    if res.code != 0:
        return None
    text = res.text
    if text.endswith("\n"):
        text = text[:-1]
    return text or None


def storage_root_ok(adb: Adb, tools: Dict[str, bool]) -> bool:
    """Shared storage must resolve to itself for the main user."""
    return canonical(adb, tools, ROOT) == ROOT


def verify_deletable(adb: Adb, tools: Dict[str, bool], path: str) -> Dict[str, object]:
    """Check on the phone that `path` is a real, plain file exactly where it
    says it is. Raises PathRejected otherwise. Fails closed: if the phone
    cannot answer the question, the answer is no."""
    info = stat_one(adb, path)
    if info is None:
        raise PathRejected("file does not exist on the phone (already gone?)")
    if not str(info["type"]).startswith("regular"):
        raise PathRejected("not a plain file (it is a %s)" % info["type"])
    real = canonical(adb, tools, path)
    if real is None:
        raise PathRejected("this phone cannot confirm where the path really points")
    if real != path:
        raise PathRejected("path resolves somewhere else on the phone (symlink)")
    return info


def delete_file(adb: Adb, path: str) -> bool:
    """Delete one exact file. True only if the phone confirms it is gone."""
    adb.shell(["rm", path], retry=False)
    return stat_one(adb, path) is None
