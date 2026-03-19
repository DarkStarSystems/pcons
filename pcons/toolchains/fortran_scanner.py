# SPDX-License-Identifier: MIT
"""Fortran module dependency scanner for Ninja dyndep.

This module scans Fortran source files for MODULE and USE statements and
produces a Ninja dyndep file that declares which object files produce and
consume which .mod files.

Run as:
    python -m pcons.toolchains.fortran_scanner \\
        --manifest fortran.manifest.json \\
        --out fortran_modules.dyndep \\
        --mod-dir modules

The manifest JSON format:
    [{"src": "/abs/path/foo.f90", "obj": "obj.mylib/foo.f90.o"}, ...]

All paths in the output are relative to the build directory (where Ninja runs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Regex for MODULE <name> declarations (produces a .mod file)
# Handles: MODULE foo, MODULE :: foo (gfortran doesn't need ::, but be flexible)
# Excludes: MODULE PROCEDURE (which is not a module definition)
_MODULE_RE = re.compile(
    r"^\s*MODULE\s+(?!PROCEDURE\b)(\w+)",
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


def write_dyndep(
    manifest: list[dict[str, str]],
    mod_dir: str,
    out_path: str,
) -> None:
    """Scan manifest sources and write Ninja dyndep file.

    Args:
        manifest: List of {"src": abs_path, "obj": build_rel_path} dicts.
        mod_dir: Module directory, relative to build dir (e.g., "modules").
        out_path: Output dyndep file path, relative to build dir.
    """
    # Scan each source file
    entries: list[tuple[str, list[str], list[str]]] = []
    for item in manifest:
        src_path = item["src"]
        obj_path = item["obj"]
        try:
            text = Path(src_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"Warning: cannot read {src_path}: {e}", file=sys.stderr)
            entries.append((obj_path, [], []))
            continue

        produces, consumes = scan_fortran_source(text)
        entries.append((obj_path, produces, consumes))

    # Write dyndep file
    lines = ["ninja_dyndep_version = 1", ""]
    for obj_path, produces, consumes in entries:
        mod_files = [f"{mod_dir}/{name}.mod" for name in produces]
        dep_files = [f"{mod_dir}/{name}.mod" for name in consumes]

        if mod_files:
            implicit_out = " | " + " ".join(mod_files)
        else:
            implicit_out = ""

        if dep_files:
            implicit_in = " | " + " ".join(dep_files)
        else:
            implicit_in = ""

        lines.append(f"build {obj_path}{implicit_out}: dyndep{implicit_in}")
        lines.append("")

    dyndep_text = "\n".join(lines)
    Path(out_path).write_text(dyndep_text, encoding="utf-8")


def main() -> int:
    """Entry point when run as python -m pcons.toolchains.fortran_scanner."""
    parser = argparse.ArgumentParser(
        description="Generate Ninja dyndep file for Fortran module dependencies"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to manifest JSON file (relative to build dir)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output dyndep file path (relative to build dir)",
    )
    parser.add_argument(
        "--mod-dir",
        default="modules",
        help="Module directory relative to build dir (default: modules)",
    )
    args = parser.parse_args()

    try:
        manifest_text = Path(args.manifest).read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading manifest {args.manifest}: {e}", file=sys.stderr)
        return 1

    write_dyndep(manifest, args.mod_dir, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
