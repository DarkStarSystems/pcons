# SPDX-License-Identifier: MIT
"""Build-time helper: verify the generate-time moc scan is still valid.

QtProgram() decides at generate time which files need moc. This edge
re-checks that decision on every build where a scanned file (or the
directory listing that resolved a header) changed, and fails loudly when
the set of moc edges would differ — e.g. a header gained Q_OBJECT after
the last pcons run. Without this, the failure mode is an undefined-vtable
link error hours later.

    python -m pcons.toolchains.qt._scan_check --manifest <json> \
        -o <stamp> --depfile <depfile>

Exit 0: scan unchanged; the stamp is only rewritten when missing (with
ninja restat, downstream edges don't rebuild). Exit 1: the message says
exactly which file changed and to re-run pcons.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pcons.toolchains.qt.scan import MocIncludeError, QtScanner


def _write_depfile(depfile: Path, stamp: Path, deps: list[str]) -> None:
    """Write a Make-style depfile (spaces escaped)."""

    def esc(s: str) -> str:
        return s.replace("\\", "/").replace(" ", "\\ ")

    depfile.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{esc(str(stamp))}: \\"]
    lines += [f"  {esc(d)} \\" for d in deps[:-1]]
    if deps:
        lines.append(f"  {esc(deps[-1])}")
    depfile.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("-o", "--output", required=True, help="Stamp file")
    parser.add_argument("--depfile", required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        print(
            "pcons Qt: unrecognized scan-manifest version "
            f"{manifest.get('version')!r} — re-run pcons to regenerate "
            "the build files.",
            file=sys.stderr,
        )
        return 1
    scanner = QtScanner(Path(manifest["project_root"]))
    result = scanner.scan_target_sources(
        [Path(p) for p in manifest["sources"]],
        include_dirs=[Path(p) for p in manifest["include_dirs"]],
        no_moc=[Path(p) for p in manifest["no_moc"]],
    )

    problems: list[str] = []
    for kind, expected_key, found in (
        ("header", "moc_headers", result.moc_headers),
        ("source", "moc_sources", result.moc_sources),
    ):
        expected = {str(Path(p)) for p in manifest[expected_key]}
        actual = {str(p) for p in found}
        for path in sorted(actual - expected):
            problems.append(f"  {path} now needs moc ({kind} gained a Qt macro)")
        for path in sorted(expected - actual):
            problems.append(f"  {path} no longer needs moc")

    if not problems:
        # A .cpp that still needs moc must still include its .moc.
        for path in result.moc_sources:
            try:
                scanner.check_moc_include(path)
            except MocIncludeError as exc:
                print(f"pcons Qt: {exc}", file=sys.stderr)
                return 1

    if problems:
        target = manifest.get("target", "?")
        print(
            f"pcons Qt: the moc scan for target '{target}' is out of date:\n"
            + "\n".join(problems)
            + "\n\nRe-run pcons to regenerate the build files.",
            file=sys.stderr,
        )
        return 1

    stamp = Path(args.output)
    deps = [str(p) for p in result.scanned] + [str(p) for p in result.scanned_dirs]
    _write_depfile(Path(args.depfile), stamp, deps)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if not stamp.exists():
        stamp.write_text("ok\n", encoding="utf-8")  # restat: new stamps only
    return 0


if __name__ == "__main__":
    sys.exit(main())
