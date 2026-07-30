# SPDX-License-Identifier: MIT
"""Generate-time scan for Qt meta-object macros (automoc).

Finds ``Q_OBJECT``/``Q_GADGET``/``Q_NAMESPACE`` in a target's sources and
their project-local headers, deciding which moc edges to create. This is
the qmake model (scan at generation, not at build), made safe:

- moc's own depfiles keep every *existing* edge incrementally correct at
  build time — the scan only decides *which* edges exist.
- A per-target staleness guard (scan manifest, checked by a cheap build
  edge) turns "the scan result would change" into a loud, actionable
  build error instead of a mysterious vtable link failure.

Scanning reads file *contents* at configure/generate time (like any
configure check) and is cached by (path, mtime, size), so warm re-runs
do no I/O beyond stat calls.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.errors import GenerateError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: Version stamp for the cache: bump when scan semantics change.
SCANNER_VERSION = 3

_MOC_MACROS = ("Q_OBJECT", "Q_GADGET", "Q_NAMESPACE_EXPORT", "Q_NAMESPACE")

# A moc macro anywhere as a standalone word. CMake's AUTOMOC only matches
# at line start and thus silently misses `class C : QObject { Q_OBJECT };`
# one-liners; comments and string literals are stripped first, so
# anywhere-matching stays accurate.
_MACRO_RE = re.compile(r"\b(" + "|".join(_MOC_MACROS) + r")\b")

# Quoted local includes: #include "foo.h" — resolved against the
# including file's directory, then the include dirs.
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)

# Angle includes: #include <foo.h> — library-style self-includes are
# common; resolved against the include dirs only (system headers won't
# resolve there and are skipped naturally).
_ANGLE_INCLUDE_RE = re.compile(r"^\s*#\s*include\s+<([^>]+)>", re.MULTILINE)

# Block comments and line comments.
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)

# String literals (after comment stripping; escaped quotes handled).
_STRING_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')

_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")

_MAX_INCLUDE_DEPTH = 32


@dataclass
class FileScan:
    """Scan result for one file."""

    macros: tuple[str, ...]  # moc macros found (empty = no moc needed)
    includes: tuple[str, ...]  # quoted #include names, verbatim
    angle_includes: tuple[str, ...] = ()  # <...> include names, verbatim


@dataclass
class TargetScan:
    """Everything automoc needs to know about one target's sources.

    Attributes:
        moc_headers: Project-local headers containing moc macros.
        moc_sources: .cpp files containing moc macros (need a .moc).
        scanned: Every file whose content influenced the result.
        scanned_dirs: Directories whose listing influenced header
            resolution (a new file appearing there can change it).
    """

    moc_headers: list[Path] = field(default_factory=list)
    moc_sources: list[Path] = field(default_factory=list)
    scanned: list[Path] = field(default_factory=list)
    scanned_dirs: list[Path] = field(default_factory=list)


class MocIncludeError(GenerateError):
    """A .cpp declares Q_OBJECT but does not #include its .moc file."""


class QtScanner:
    """Content scanner with an on-disk cache.

    The cache lives in the build directory (qt-scan-cache.json) keyed by
    absolute path + mtime + size; a warm re-run stats files but reads
    nothing.
    """

    def __init__(self, project_root: Path, cache_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self._cache_path = (
            cache_dir / "qt-scan-cache.json" if cache_dir is not None else None
        )
        self._cache: dict[str, dict] = {}
        self._dirty = False
        self._load_cache()

    # -- cache ------------------------------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if data.get("version") == SCANNER_VERSION:
            self._cache = data.get("files", {})

    def save_cache(self) -> None:
        if self._cache_path is None or not self._dirty:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: a concurrent pcons in the same build dir must
        # never observe a torn cache file.
        tmp = self._cache_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"version": SCANNER_VERSION, "files": self._cache}),
            encoding="utf-8",
        )
        os.replace(tmp, self._cache_path)
        self._dirty = False

    # -- single-file scan --------------------------------------------------

    def scan_file(self, path: Path) -> FileScan:
        """Scan one file for moc macros and includes (cached)."""
        try:
            stat = path.stat()
        except OSError:
            return FileScan(macros=(), includes=())
        key = str(path.resolve())
        entry = self._cache.get(key)
        if (
            entry is not None
            and entry.get("mtime_ns") == stat.st_mtime_ns
            and entry.get("size") == stat.st_size
        ):
            return FileScan(
                macros=tuple(entry["macros"]),
                includes=tuple(entry["includes"]),
                angle_includes=tuple(entry.get("angle_includes", ())),
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return FileScan(macros=(), includes=())
        stripped = _COMMENT_RE.sub("", text)
        # Includes are extracted before string-stripping (the include
        # path IS a string literal); macros after, to avoid false hits
        # on "Q_OBJECT" appearing in message strings.
        includes = tuple(dict.fromkeys(_INCLUDE_RE.findall(stripped)))
        angle_includes = tuple(dict.fromkeys(_ANGLE_INCLUDE_RE.findall(stripped)))
        macros = tuple(dict.fromkeys(_MACRO_RE.findall(_STRING_RE.sub('""', stripped))))

        self._cache[key] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "macros": list(macros),
            "includes": list(includes),
            "angle_includes": list(angle_includes),
        }
        self._dirty = True
        return FileScan(macros=macros, includes=includes, angle_includes=angle_includes)

    # -- target scan -------------------------------------------------------

    def _in_project(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.project_root)
            return True
        except ValueError:
            return False

    def scan_target_sources(
        self,
        sources: Iterable[Path],
        include_dirs: Sequence[Path] = (),
        no_moc: Iterable[Path] = (),
    ) -> TargetScan:
        """Scan a target's C++ sources and their local header closure.

        For each source: the source itself is scanned (moc macros in a
        .cpp mean it needs a generated ``<stem>.moc``, which it must
        #include — enforced by the caller via check_moc_include()); its
        same-basename header and the closure of its includes are scanned
        for header-mode moc. Quoted includes resolve against the
        including file's directory then include_dirs; angle includes
        against include_dirs only. A resolved header is followed when it
        lives in the project OR under one of the given include dirs
        (which may point outside the project — sibling repos, vendored
        libraries); system headers resolve nowhere here and are skipped.

        Args:
            sources: The target's C++ source files (absolute or
                project-relative paths).
            include_dirs: The target's include dirs, used both to
                resolve includes and as additional scan roots.
            no_moc: Files to exclude from moc even if they match.

        Returns:
            A TargetScan; moc_headers/moc_sources are sorted for stable
            edge ordering.
        """
        result = TargetScan()
        excluded = {Path(p).resolve() for p in no_moc}
        include_dirs = [Path(d).resolve() for d in include_dirs]
        scan_roots = [self.project_root, *include_dirs]

        def in_scan_roots(path: Path) -> bool:
            return any(path.is_relative_to(root) for root in scan_roots)

        def resolve_include(name: str, from_dir: Path | None) -> Path | None:
            bases = ([from_dir] if from_dir is not None else []) + include_dirs
            for base in bases:
                candidate = (base / name).resolve()
                if candidate.is_file() and in_scan_roots(candidate):
                    return candidate
            return None

        visited: set[Path] = set()
        moc_headers: set[Path] = set()
        moc_sources: set[Path] = set()
        scanned_dirs: set[Path] = set()

        def follow_includes(scan: FileScan, from_dir: Path, depth: int) -> None:
            for name, quoted in (
                *((inc, True) for inc in scan.includes),
                *((inc, False) for inc in scan.angle_includes),
            ):
                resolved = resolve_include(name, from_dir if quoted else None)
                if resolved is not None and resolved.suffix in _HEADER_SUFFIXES:
                    visit_header(resolved, depth)

        def visit_header(path: Path, depth: int) -> None:
            path = path.resolve()
            if path in visited or depth > _MAX_INCLUDE_DEPTH:
                return
            visited.add(path)
            scanned_dirs.add(path.parent)
            scan = self.scan_file(path)
            if scan.macros and path not in excluded:
                moc_headers.add(path)
            follow_includes(scan, path.parent, depth + 1)

        for source in sources:
            source = Path(source).resolve()
            if not in_scan_roots(source):
                continue
            if source.suffix in _HEADER_SUFFIXES:
                # Headers listed directly in sources (the CMake/qmake
                # convention) are scanned like any discovered header.
                visit_header(source, 1)
                continue
            visited.add(source)
            scanned_dirs.add(source.parent)
            scan = self.scan_file(source)
            if scan.macros and source not in excluded:
                moc_sources.add(source)
            # The same-basename header is scanned even when not included
            # by its own .cpp (a class used only via other TUs).
            for suffix in _HEADER_SUFFIXES:
                sibling = source.with_suffix(suffix)
                if sibling.is_file():
                    visit_header(sibling, 1)
            follow_includes(scan, source.parent, 1)

        result.moc_headers = sorted(moc_headers)
        result.moc_sources = sorted(moc_sources)
        result.scanned = sorted(visited)
        result.scanned_dirs = sorted(scanned_dirs)
        return result

    def check_moc_include(self, source: Path) -> None:
        """Require ``#include "<stem>.moc"`` in a moc'ed source file.

        Without the include the meta-object code is silently never
        compiled — the classic CMake/qmake trap that surfaces later as
        undefined-vtable link errors. Fail now, with the fix spelled out.
        """
        expected = f"{Path(source).stem}.moc"
        scan = self.scan_file(Path(source))
        if expected not in scan.includes:
            raise MocIncludeError(
                f"{source} declares a Qt meta-object macro (Q_OBJECT/"
                f"Q_GADGET) but never includes its moc output.\n"
                f'Add this line at the end of the file:\n\n    #include "{expected}"\n'
            )
