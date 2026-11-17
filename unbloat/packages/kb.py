"""The package knowledge base: JSON files, one per brand family."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .denylist import core_android, denied, valid_package_name

KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kb")
TIERS = ("safe", "caution", "risky", "never")
FAMILIES = ("common", "samsung", "xiaomi", "oppo", "vivo", "motorola", "pixel", "nothing")
ENTRY_KEYS = {"package", "name", "what", "tier", "breaks", "applies_to"}
FILE_KEYS = {"family", "description", "packages"}


def validate_file(path: str) -> List[str]:
    """Every problem found in one knowledge-base file."""
    name = os.path.basename(path)
    problems: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return ["%s: cannot be read as JSON (%s)" % (name, exc)]
    if not isinstance(doc, dict):
        return ["%s: top level must be an object" % name]
    extra = set(doc) - FILE_KEYS
    if extra:
        problems.append("%s: unknown top-level keys %s" % (name, sorted(extra)))
    family = doc.get("family")
    if family not in FAMILIES:
        problems.append("%s: 'family' must be one of %s" % (name, ", ".join(FAMILIES)))
    elif name != family + ".json":
        problems.append("%s: file name must match its family (%s.json)" % (name, family))
    entries = doc.get("packages")
    if not isinstance(entries, list):
        return problems + ["%s: 'packages' must be a list" % name]
    seen = set()
    for i, e in enumerate(entries):
        where = "%s entry %d" % (name, i + 1)
        if not isinstance(e, dict):
            problems.append("%s: must be an object" % where)
            continue
        pkg = e.get("package")
        if not valid_package_name(pkg):
            problems.append("%s: bad or missing package name %r" % (where, pkg))
            continue
        where = "%s (%s)" % (name, pkg)
        if pkg in seen:
            problems.append("%s: listed twice" % where)
        seen.add(pkg)
        extra = set(e) - ENTRY_KEYS
        if extra:
            problems.append("%s: unknown keys %s" % (where, sorted(extra)))
        for key in ("name", "what", "breaks"):
            v = e.get(key)
            if not isinstance(v, str) or not v.strip():
                problems.append("%s: '%s' must be a non-empty sentence" % (where, key))
        tier = e.get("tier")
        if tier not in TIERS:
            problems.append("%s: tier must be one of %s" % (where, ", ".join(TIERS)))
        applies = e.get("applies_to", "")
        if not isinstance(applies, str):
            problems.append("%s: 'applies_to' must be text" % where)
        why = denied(pkg)
        if why and tier != "never":
            problems.append("%s: is on the never-disable list (%s) so its tier must be 'never'" % (where, why))
    return problems


def validate_all(kb_dir: str = KB_DIR) -> List[str]:
    problems: List[str] = []
    found = set()
    owners: Dict[str, str] = {}
    for fname in sorted(os.listdir(kb_dir)):
        if not fname.endswith(".json"):
            continue
        found.add(fname[:-5])
        path = os.path.join(kb_dir, fname)
        file_problems = validate_file(path)
        problems.extend(file_problems)
        if file_problems:
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for e in json.load(fh)["packages"]:
                if e["package"] in owners and owners[e["package"]] != fname:
                    problems.append("%s is listed in both %s and %s" % (e["package"], owners[e["package"]], fname))
                owners[e["package"]] = fname
    for family in FAMILIES:
        if family not in found:
            problems.append("missing knowledge-base file %s.json" % family)
    return problems


_cache: Dict[str, Dict[str, Dict[str, str]]] = {}


def load_family(family: str, kb_dir: str = KB_DIR) -> Dict[str, Dict[str, str]]:
    key = kb_dir + "|" + family
    if key not in _cache:
        path = os.path.join(kb_dir, family + ".json")
        entries: Dict[str, Dict[str, str]] = {}
        if os.path.isfile(path) and not validate_file(path):
            with open(path, "r", encoding="utf-8") as fh:
                for e in json.load(fh)["packages"]:
                    entries[e["package"]] = e
        _cache[key] = entries
    return _cache[key]


def lookup(package: str, family: str) -> Dict[str, Optional[str]]:
    """What we know about a package on this brand of phone.

    The never-disable list always wins. A package we have no entry for is
    'unknown': it is not guessed to be safe.
    """
    entry = None
    if family in FAMILIES and family != "common":
        entry = load_family(family).get(package)
    if entry is None:
        entry = load_family("common").get(package)
    why = denied(package)
    if why:
        return {
            "tier": "never",
            "name": entry["name"] if entry else None,
            "what": entry["what"] if entry else "Needed for %s." % why,
            "breaks": entry["breaks"] if entry else "The phone can stop working properly: %s." % why,
        }
    if entry is None and core_android(package):
        return {
            "tier": "never",
            "name": None,
            "what": "A part of base Android that unbloat has no reviewed entry for.",
            "breaks": "Unknown. Base Android parts are left alone unless they have been reviewed.",
        }
    if entry is None:
        return {"tier": "unknown", "name": None, "what": None, "breaks": None}
    return {"tier": entry["tier"], "name": entry["name"], "what": entry["what"], "breaks": entry["breaks"]}
