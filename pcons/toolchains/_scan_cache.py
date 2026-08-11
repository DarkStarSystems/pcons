# SPDX-License-Identifier: MIT
"""Persist C++ module scan results, so an unchanged TU is not rescanned.

A p1689 scan is a full preprocessor run: ~40 ms per translation unit, and every
pcons invocation repeated all of them. GCC already tells us exactly what each
scan read, via the ``-MD -MF`` depfile the scanner asks for and used to discard.
That prerequisite list is the invalidation key: if none of those files changed,
last run's answer still stands.

Stamps are (mtime_ns, size), not content hashes. Hashing a TU's ~300
prerequisites would cost more than the scan it saves, and a false miss only
costs one scan.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_FILE = "pcons_scan_cache.pkl"

#: What produced the stored answers. Part of every key, so a pcons whose scan
#: command differs rescans instead of trusting the old one.
RECIPE: str = "gcc-p1689r5-directives-only-1"


def parse_depfile(text: str) -> list[str]:
    """The prerequisites out of a make-style depfile.

    Not `text.split()`: a depfile continues lines with a trailing backslash and
    escapes a literal space in a path as ``\\ ``, which is the common case on
    Windows. Splitting on whitespace would turn one path into two, and two
    missing files into a permanent cache miss.
    """
    prereqs: list[str] = []
    current: list[str] = []
    index = 0
    seen_colon = False

    while index < len(text):
        char = text[index]
        index += 1

        if char == "\\" and index < len(text):
            following = text[index]
            if following == "\n":
                index += 1  # line continuation, not part of any path
                continue
            if following in " \t#\\":
                current.append(following)  # escaped literal
                index += 1
                continue

        if char == ":" and not seen_colon:
            # Everything before the first unescaped colon is the target.
            seen_colon = True
            current = []
            continue

        if char in " \t\n":
            if current:
                prereqs.append("".join(current))
                current = []
            continue

        current.append(char)

    if current:
        prereqs.append("".join(current))
    return prereqs


def _stamp(path: str) -> tuple[int, int] | None:
    """(mtime_ns, size), or None when the file is gone."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


class ScanCache:
    """Scan results for one build directory, keyed by what was scanned.

    Reads are lock-free (the dict is not written during a scan pass until
    `put`), writes take a lock: `scan_translation_units` runs its scans on a
    thread pool.
    """

    def __init__(self, build_dir: Path) -> None:
        self._path = build_dir / CACHE_FILE
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("rb") as f:
                data = pickle.load(f)
        except (OSError, pickle.PickleError) as e:
            logger.warning("Unreadable scan cache %s: %s - rescanning", self._path, e)
            return
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            self._entries = data["entries"]

    @staticmethod
    def key(compiler: str, compile_flags: list[str], src: str) -> str:
        """A different compiler or flag set is a different question.

        Not a stale answer to the same one, so it gets its own entry rather
        than invalidating the old.

        `RECIPE` covers what the caller cannot see: the flags the scan command
        adds for itself. Bump it whenever that command changes, or an upgraded
        pcons will reuse answers the old command produced.
        """
        material = "\0".join([RECIPE, compiler, src, *compile_flags])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        """Last run's p1689 for *key*, if every prerequisite is untouched."""
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            return None
        prereqs = entry.get("prereqs")
        stamps = entry.get("stamps")
        if not isinstance(prereqs, list) or not isinstance(stamps, list):
            return None
        if len(prereqs) != len(stamps):
            return None
        for path, stamp in zip(prereqs, stamps, strict=True):
            current = _stamp(path)
            if current is None or list(current) != list(stamp):
                return None
        p1689 = entry.get("p1689")
        return p1689 if isinstance(p1689, dict) else None

    def put(self, key: str, p1689: dict[str, Any], prereqs: list[str]) -> None:
        """Record a scan, with the files it read, for the next run.

        Prerequisites are stored absolute: a depfile writes them as the
        compiler saw them, relative to the directory the scan ran in, and a
        later run stats them from wherever pcons was started.
        """
        resolved = [os.path.abspath(p) for p in prereqs]
        stamps = [_stamp(p) for p in resolved]
        if any(s is None for s in stamps):
            # A prerequisite vanished between the scan and now. Storing it
            # would produce an entry that can never hit.
            return
        with self._lock:
            self._entries[key] = {
                "p1689": p1689,
                "prereqs": resolved,
                "stamps": stamps,
            }
            self._dirty = True

    def save(self) -> None:
        """Write the cache out, once, at the end of a scan pass."""
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_bytes(
                pickle.dumps(
                    {"entries": self._entries}, protocol=pickle.HIGHEST_PROTOCOL
                )
            )
        except OSError as e:
            # A cache that cannot be written is a slow build, not a failed one.
            logger.warning("Could not write scan cache %s: %s", self._path, e)
        else:
            self._dirty = False
