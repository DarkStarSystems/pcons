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
writes into the module directory; because that file is not one of the
compile's declared outputs, each provided ``.mod`` is also reported as an
extra output, which is what makes it a dyndep implicit output. ``USE bar``
requires ``bar``, unless this same file provides it or it is an intrinsic
module.

``SUBMODULE (foo) impl`` requires its parent ``foo`` and provides
``foo:impl``, whose file the compiler names (``--submodule-file``, e.g.
``{ancestor}@{name}.smod`` for gfortran); a submodule of it,
``SUBMODULE (foo:impl) more``, requires ``foo:impl``. A submodule sees its
ancestor's private entities too, which gfortran keeps out of ``foo.mod``
and writes to ``foo.smod`` (``--ancestor-file``), for a module that
declares a separate module procedure. There, ``foo`` also provides
``foo:`` at that file, and ``SUBMODULE (foo) impl`` requires ``foo:``
instead, so a change to a private entity recompiles it. Module names are
lowercased throughout: Fortran is case-insensitive and compilers write
lowercase module files.

Limitations: Fortran ``INCLUDE`` lines are not followed, so a module
declaration reached only through an include is invisible here; and the
preprocessor doesn't run, so a declaration ``#if`` leaves out still counts.

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

# Regex for MODULE <name> declarations (produces a .mod file): the name ends
# the statement. That excludes MODULE PROCEDURE lists and the separate module
# procedures a submodule implements ("module subroutine step()", "module pure
# function f(x)"), none of which defines a module.
_MODULE_RE = re.compile(
    r"^\s*MODULE\s+(\w+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Regex for SUBMODULE (<ancestor>[:<parent>]) <name> declarations
_SUBMODULE_RE = re.compile(
    r"^\s*SUBMODULE\s*\(\s*(\w+)\s*(?::\s*(\w+)\s*)?\)\s*(\w+)",
    re.IGNORECASE | re.MULTILINE,
)

# How gfortran and ifx name a submodule's file; flang uses {ancestor}-{name}.mod.
DEFAULT_SUBMODULE_FILE = "{ancestor}@{name}.smod"

# The file a submodule reads from its ancestor module: the module's own .mod
# for flang and ifx; gfortran's is {name}.smod.
DEFAULT_ANCESTOR_FILE = "{name}.mod"

# A separate module procedure's interface: "module" among the prefixes of a
# function or subroutine statement ("module function f()", "integer module
# function f()", "pure module subroutine s(x)"). Not "module procedure",
# and [ \t] keeps it to one line: "end module" ending one line must not
# join a "subroutine s" starting the next.
_SEPARATE_PROCEDURE_RE = re.compile(
    r"^[ \t]*(?:\w+(?:[ \t]*\([^)\n]*\))?[ \t]+)*?MODULE[ \t]+"
    r"(?:\w+(?:[ \t]*\([^)\n]*\))?[ \t]+)*?(?:FUNCTION|SUBROUTINE)[ \t]+\w",
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

# A free-form continuation: "&" ending a line, and optionally starting the next.
_CONTINUATION_RE = re.compile(r"&[ \t]*\n[ \t]*&?")

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


def _statements(source: str) -> str:
    """*source* without comments, one statement per line: continuations
    joined, and ";"-separated statements split."""
    joined = _CONTINUATION_RE.sub(" ", strip_comments(source))
    return joined.replace(";", "\n")


def scan_fortran_source(
    source_text: str, ancestor_file_apart: bool = False
) -> tuple[list[str], list[str]]:
    """Scan Fortran source for MODULE and USE statements.

    Args:
        source_text: Content of the Fortran source file.
        ancestor_file_apart: Whether a module writes its submodules a file
            of their own (gfortran's .smod) when it declares a separate
            module procedure.

    Returns:
        Tuple of (produces, consumes) where:
        - produces: list of module names this file defines (lowercase);
          a submodule is "<ancestor>:<name>", and with ancestor_file_apart
          a module declaring a separate module procedure is also "<name>:"
        - consumes: list of module names this file uses (lowercase);
          a submodule uses its parent, "<ancestor>" ("<ancestor>:" with
          ancestor_file_apart) or "<ancestor>:<parent>"
    """
    clean = _statements(source_text)

    modules = list(_MODULE_RE.finditer(clean))
    produces = [m.group(1).lower() for m in modules]
    used = [m.group(1).lower() for m in _USE_RE.finditer(clean)]
    submodules = list(_SUBMODULE_RE.finditer(clean))
    for m in submodules:
        ancestor, parent, name = (g.lower() if g else g for g in m.groups())
        produces.append(f"{ancestor}:{name}")
        if parent:
            used.append(f"{ancestor}:{parent}")
        else:
            used.append(f"{ancestor}:" if ancestor_file_apart else ancestor)
    if ancestor_file_apart:
        produces += _modules_declaring_separate_procedures(modules, submodules, clean)

    consumes = []
    seen: set[str] = set()
    for name in used:
        # Skip intrinsics and self-references
        if name not in _INTRINSIC_MODULES and name not in seen:
            consumes.append(name)
            seen.add(name)

    # Remove any module from consumes that is defined in the same file
    # (e.g., a module that uses its own sub-module interface)
    produces_set = set(produces)
    consumes = [c for c in consumes if c not in produces_set]

    return produces, consumes


def _modules_declaring_separate_procedures(
    modules: list[re.Match[str]], submodules: list[re.Match[str]], source: str
) -> list[str]:
    """ "<name>:" for each module that declares a separate module procedure,
    the procedure taken to belong to the nearest MODULE or SUBMODULE above."""
    units = sorted(
        [(m.start(), m.group(1).lower()) for m in modules]
        + [(m.start(), None) for m in submodules]
    )
    found: dict[str, None] = {}
    for procedure in _SEPARATE_PROCEDURE_RE.finditer(source):
        above = [name for start, name in units if start < procedure.start()]
        if above and above[-1] is not None:
            found[f"{above[-1]}:"] = None
    return list(found)


def scan_info(
    source_text: str,
    moddir: str,
    submodule_file: str = DEFAULT_SUBMODULE_FILE,
    ancestor_file: str = DEFAULT_ANCESTOR_FILE,
) -> dict[str, object]:
    """The scan-info document for one Fortran source.

    Each provided module's file appears in both ``provides`` (so requiring
    edges can depend on it) and ``extra_outputs`` (so the dyndep file claims
    the compile writes it) -- the contract collate enforces.
    """
    apart = ancestor_file != DEFAULT_ANCESTOR_FILE
    produces, consumes = scan_fortran_source(source_text, apart)
    mod_dir = moddir.rstrip("/") or "."
    provided = list(dict.fromkeys(produces))

    def module_file(name: str) -> str:
        ancestor, colon, submodule = name.partition(":")
        if not colon:
            return f"{name}.mod"
        if not submodule:
            return ancestor_file.format(name=ancestor)
        return submodule_file.format(ancestor=ancestor, name=submodule)

    mod_paths = [f"{mod_dir}/{module_file(name)}" for name in provided]
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
        "--submodule-file",
        default=DEFAULT_SUBMODULE_FILE,
        help="file a submodule compiles to, from {ancestor} and {name} "
        f"(default: {DEFAULT_SUBMODULE_FILE})",
    )
    parser.add_argument(
        "--ancestor-file",
        default=DEFAULT_ANCESTOR_FILE,
        help="file a submodule reads from its ancestor module, from {name} "
        f"(default: {DEFAULT_ANCESTOR_FILE})",
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

    info = scan_info(text, args.moddir, args.submodule_file, args.ancestor_file)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_if_changed(out, json.dumps(info, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
