# SPDX-License-Identifier: MIT
"""Fortran module scanner: the scan half of pcons's Scanner primitive.

One invocation reads one Fortran source and reports what it provides and
requires as a scan-info JSON document (schema in :mod:`pcons.core.collate`);
the generic collate turns a scope's documents into a Ninja dyndep file.

Run as one scan edge::

    python -m pcons.toolchains.fortran_scanner \\
        --scan-one src/greetings.f90 \\
        --moddir modules \\
        --out build/obj.hello/src/greetings.f90.o.fscan.json

``MODULE foo`` provides ``foo`` at ``<moddir>/foo.mod``, which the compile
writes via ``-J <moddir>``; because that file is not one of the compile's
declared outputs, each provided ``.mod`` is also reported as an extra output,
which is what makes it a dyndep implicit output. ``USE bar`` requires ``bar``,
unless this same file provides it or it is an intrinsic module. Module names
are lowercased throughout: Fortran is case-insensitive and gfortran writes
lowercase ``.mod`` files.

Limitation: Fortran ``INCLUDE`` lines are not followed, so a module
declaration reached only through an include is invisible here.

Paths in the output are relative to the build directory, where Ninja runs.
"""

from __future__ import annotations

# argparse, not click: a build-edge subprocess, where click costs ~14ms of
# import per invocation. The CLI here is internal, typed only by pcons's
# own toolchains.
import argparse
import json
import re
import sys
from pathlib import Path

from pcons.core.collate import SCAN_INFO_VERSION, write_text_if_changed

# Regex for MODULE <name> declarations (produces a .mod file)
# Handles: MODULE foo, MODULE :: foo (gfortran doesn't need ::, but be flexible)
# Excludes: MODULE PROCEDURE, and the "module subroutine" / "module function"
# a submodule uses to implement a separate module procedure; neither defines
# a module.
_MODULE_RE = re.compile(
    r"^\s*MODULE\s+(?!PROCEDURE\b|SUBROUTINE\b|FUNCTION\b)(\w+)",
    re.IGNORECASE | re.MULTILINE,
)

# Regex for USE <name> statements (consumes a .mod file)
# Handles: USE foo, USE :: foo, USE foo, ONLY: bar
# Does not match USE statements inside string literals (good enough for real code)
_USE_RE = re.compile(
    r"^\s*USE\s+(?:::\s*)?(\w+)",
    re.IGNORECASE | re.MULTILINE,
)

# Regex to detect Fortran inline comments (! starts a comment)
_COMMENT_RE = re.compile(r"!.*$", re.MULTILINE)

# Intrinsic modules that should not create .mod dependencies
_INTRINSIC_MODULES = frozenset(
    [
        "iso_c_binding",
        "iso_fortran_env",
        "ieee_arithmetic",
        "ieee_exceptions",
        "ieee_features",
        "omp_lib",
        "omp_lib_kinds",
        "mpi",
        "mpi_f08",
    ]
)


def strip_comments(source: str) -> str:
    """Strip Fortran inline comments from source text."""
    return _COMMENT_RE.sub("", source)


def scan_fortran_source(source_text: str) -> tuple[list[str], list[str]]:
    """Scan Fortran source for MODULE and USE statements.

    Args:
        source_text: Content of the Fortran source file.

    Returns:
        Tuple of (produces, consumes) where:
        - produces: list of module names this file defines (lowercase)
        - consumes: list of module names this file uses (lowercase)
    """
    clean = strip_comments(source_text)

    produces = []
    for m in _MODULE_RE.finditer(clean):
        name = m.group(1).lower()
        produces.append(name)

    consumes = []
    seen: set[str] = set()
    for m in _USE_RE.finditer(clean):
        name = m.group(1).lower()
        # Skip intrinsics and self-references
        if name not in _INTRINSIC_MODULES and name not in seen:
            consumes.append(name)
            seen.add(name)

    # Remove any module from consumes that is defined in the same file
    # (e.g., a module that uses its own sub-module interface)
    produces_set = set(produces)
    consumes = [c for c in consumes if c not in produces_set]

    return produces, consumes


def scan_info(source_text: str, moddir: str) -> dict[str, object]:
    """The scan-info document for one Fortran source.

    Each provided module's ``.mod`` file appears in both ``provides`` (so
    requiring edges can depend on it) and ``extra_outputs`` (so the dyndep
    file claims the compile writes it) -- the contract collate enforces.
    """
    produces, consumes = scan_fortran_source(source_text)
    mod_dir = moddir.rstrip("/") or "."
    provided = list(dict.fromkeys(produces))
    mod_paths = [f"{mod_dir}/{name}.mod" for name in provided]
    return {
        "version": SCAN_INFO_VERSION,
        "provides": [
            {"name": name, "path": path}
            for name, path in zip(provided, mod_paths, strict=True)
        ],
        "requires": consumes,
        "extra_outputs": mod_paths,
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point when run as python -m pcons.toolchains.fortran_scanner."""
    parser = argparse.ArgumentParser(
        prog="python -m pcons.toolchains.fortran_scanner",
        description="Report one Fortran source's module provides and requires.",
    )
    parser.add_argument(
        "--scan-one",
        required=True,
        metavar="SOURCE",
        help="Fortran source file to scan",
    )
    parser.add_argument(
        "--moddir",
        default="modules",
        help="module directory relative to the build dir (default: modules)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="scan-info JSON file to write",
    )
    args = parser.parse_args(argv)

    try:
        text = Path(args.scan_one).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"pcons fortran scan: cannot read {args.scan_one}: {e}", file=sys.stderr)
        return 1

    info = scan_info(text, args.moddir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_if_changed(out, json.dumps(info, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
