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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: Version stamp for the cache: bump when scan semantics change.
SCANNER_VERSION = 2

_MOC_MACROS = ("Q_OBJECT", "Q_GADGET", "Q_NAMESPACE_EXPORT", "Q_NAMESPACE")

# A moc macro anywhere as a standalone word. CMake's AUTOMOC only matches
# at line start and thus silently misses `class C : QObject { Q_OBJECT };`
# one-liners; comments and string literals are stripped first, so
# anywhere-matching stays accurate.
_MACRO_RE = re.compile(r"\b(" + "|".join(_MOC_MACROS) + r")\b")

# Quoted local includes: #include "foo.h" (angle includes are external).
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)

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


class MocIncludeError(RuntimeError):
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
        self._cache_path.write_text(
            json.dumps({"version": SCANNER_VERSION, "files": self._cache})
        )
        self._dirty = False

    # -- single-file scan --------------------------------------------------

    def scan_file(self, path: Path) -> FileScan:
        """Scan one file for moc macros and quoted includes (cached)."""
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
                macros=tuple(entry["macros"]), includes=tuple(entry["includes"])
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
        macros = tuple(dict.fromkeys(_MACRO_RE.findall(_STRING_RE.sub('""', stripped))))

        self._cache[key] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "macros": list(macros),
            "includes": list(includes),
        }
        self._dirty = True
        return FileScan(macros=macros, includes=includes)

    # -- target scan -------------------------------------------------------

    def _in_project(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.project_root)
            return True
        except ValueError:
            return False

    def _resolve_include(
        self, name: str, from_dir: Path, include_dirs: Sequence[Path]
    ) -> Path | None:
        """Resolve a quoted include to a project-local file, or None."""
        for base in (from_dir, *include_dirs):
            candidate = (base / name).resolve()
            if candidate.is_file() and self._in_project(candidate):
                return candidate
        return None

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
        same-basename header and the closure of quoted includes resolved
        inside the project are scanned for header-mode moc.

        Args:
            sources: The target's C++ source files (absolute or
                project-relative paths).
            include_dirs: Project-local include dirs for resolving
                quoted #includes.
            no_moc: Files to exclude from moc even if they match.

        Returns:
            A TargetScan; moc_headers/moc_sources are sorted for stable
            edge ordering.
        """
        result = TargetScan()
        excluded = {Path(p).resolve() for p in no_moc}
        local_includes = [d for d in include_dirs if self._in_project(d)]
        visited: set[Path] = set()
        moc_headers: set[Path] = set()
        moc_sources: set[Path] = set()
        scanned_dirs: set[Path] = set()

        def visit_header(path: Path, depth: int) -> None:
            path = path.resolve()
            if path in visited or depth > _MAX_INCLUDE_DEPTH:
                return
            visited.add(path)
            scanned_dirs.add(path.parent)
            scan = self.scan_file(path)
            if scan.macros and path not in excluded:
                moc_headers.add(path)
            for include in scan.includes:
                resolved = self._resolve_include(include, path.parent, local_includes)
                if resolved is not None and resolved.suffix in _HEADER_SUFFIXES:
                    visit_header(resolved, depth + 1)

        for source in sources:
            source = Path(source).resolve()
            if not self._in_project(source):
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
            for include in scan.includes:
                resolved = self._resolve_include(include, source.parent, local_includes)
                if resolved is not None and resolved.suffix in _HEADER_SUFFIXES:
                    visit_header(resolved, 1)

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
