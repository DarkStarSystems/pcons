# SPDX-License-Identifier: MIT
"""Build-time automoc: scan for Qt macros, run moc, write the aggregator.

One edge per Qt target. It reads the target's sources and their header
closure, runs moc on everything carrying Q_OBJECT/Q_GADGET/Q_NAMESPACE, and
writes ``mocs_compilation.cpp``, a translation unit that ``#include``s every
``moc_*.cpp`` it produced. That TU is the edge's one static output, so the
moc set is decided while the build runs and a generated header reaches it
like any other input.

    python -m pcons.toolchains.qt._automoc --spec <json> \
        -o <mocs_compilation.cpp> --depfile <depfile>

The command line stays argparse rather than click: it runs under
``$qt.python``, which the user may point at an interpreter that has pcons but
none of pcons' own dependencies.

The depfile names every file read and every directory listed, by the scan and
by moc alike, so the edge re-runs exactly when the set or any moc output could
change. Ninja does not know the individual ``moc_*.cpp`` and ``*.moc`` files,
so this tool owns them: it records what it produced and deletes what it no
longer produces.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from pcons.toolchains.qt.scan import MocIncludeError, QtScanner, output_rel_dir

SPEC_VERSION = 1

_EMPTY_TU = "enum pcons_automoc_empty { pcons_automoc_needs_more_than_nothing };\n"


def _esc(path: str) -> str:
    return path.replace("\\", "/").replace(" ", "\\ ")


def _write_depfile(depfile: Path, target: Path, deps: list[str]) -> None:
    """Write a Make-style depfile (spaces escaped)."""
    depfile.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{_esc(str(target))}: \\"]
    lines += [f"  {_esc(d)} \\" for d in deps[:-1]]
    if deps:
        lines.append(f"  {_esc(deps[-1])}")
    depfile.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_depfile(depfile: Path) -> list[Path]:
    """The prerequisites a Make-style depfile lists, unescaped."""
    try:
        text = depfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    _, _, rest = text.partition(":")
    rest = rest.replace("\\\r\n", " ").replace("\\\n", " ")
    deps: list[Path] = []
    token = ""
    index = 0
    while index < len(rest):
        char = rest[index]
        if char == "\\" and index + 1 < len(rest) and rest[index + 1] == " ":
            token += " "
            index += 2
            continue
        if char.isspace():
            if token:
                deps.append(Path(token))
                token = ""
        else:
            token += char
        index += 1
    if token:
        deps.append(Path(token))
    return deps


def _write_if_changed(path: Path, content: str) -> bool:
    """Write *content* only when it differs; return whether it was written."""
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _write_aggregator(path: Path, content: str, moc_ran: bool) -> None:
    """Write the aggregator TU, unconditionally when moc produced anything.

    The edge is ``restat``, so leaving an unchanged aggregator alone keeps
    ninja from recompiling it. That is right when the edge only re-scanned.
    It is wrong when moc re-ran: moc's inputs other than the sources (the
    spec's flags and defines, ``moc_predefs.h``) can change every
    ``moc_X.cpp`` while the aggregator stays byte-identical, and ninja
    cleans the dependents of a restat output against the mtimes it cached
    before the edge ran, so it never sees the new ``moc_X.cpp``. Bumping
    the aggregator forces the one recompile that carries them.
    """
    if moc_ran:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return
    _write_if_changed(path, content)


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _needs_moc(output: Path, source: Path, floor: int) -> bool:
    """Whether moc must run again for *source*.

    Ninja never sees these outputs, so their freshness is this tool's
    question. moc's own depfile from the previous run names the headers the
    output depends on; *floor* covers what the spec and the compiler
    predefines contribute.
    """
    stamp = _mtime(output)
    if stamp < 0:
        return True
    if floor > stamp or _mtime(source) > stamp:
        return True
    return any(
        _mtime(dep) > stamp
        for dep in _read_depfile(output.with_name(output.name + ".d"))
    )


def _run_moc(moc: list[str], args: list[str], output: Path, source: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        *moc,
        *args,
        "--output-dep-file",
        "--dep-file-path",
        f"{output}.d",
        "-o",
        str(output),
        str(source),
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"pcons Qt: moc failed on {source}", file=sys.stderr)
    return result.returncode


def _collect_json(moc: list[str], sidecars: list[Path], metatypes: Path) -> int:
    """Merge moc's JSON sidecars into *metatypes*, only when it changes.

    The file is a declared output of a static edge, so it must exist even
    when nothing was moc'ed; ``moc --collect-json`` refuses an empty input
    list, so the empty document is written directly.
    """
    metatypes.parent.mkdir(parents=True, exist_ok=True)
    if not sidecars:
        _write_if_changed(metatypes, "[]\n")
        return 0
    temp = metatypes.with_name(metatypes.name + ".tmp")
    result = subprocess.run(
        [*moc, "--collect-json", "-o", str(temp), *(str(p) for p in sidecars)],
        check=False,
    )
    if result.returncode != 0:
        print("pcons Qt: moc --collect-json failed", file=sys.stderr)
        temp.unlink(missing_ok=True)
        return result.returncode
    _write_if_changed(metatypes, temp.read_text(encoding="utf-8"))
    temp.unlink(missing_ok=True)
    return 0


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".d").unlink(missing_ok=True)
    path.with_name(path.name + ".json").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("-o", "--output", required=True, help="The aggregator TU")
    parser.add_argument("--depfile", required=True)
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if spec.get("version") != SPEC_VERSION:
        print(
            f"pcons Qt: unrecognized automoc spec version {spec.get('version')!r} — "
            "re-run pcons to regenerate the build files.",
            file=sys.stderr,
        )
        return 1

    project_root = Path(spec["project_root"])
    gen_dir = Path(spec["gen_dir"])
    aggregator = Path(args.output)
    scanner = QtScanner(project_root, cache_dir=gen_dir)
    result = scanner.scan_target_sources(
        [Path(p) for p in spec["sources"]],
        include_dirs=[Path(p) for p in spec["include_dirs"]],
        no_moc=[Path(p) for p in spec["no_moc"]],
    )

    for source in result.moc_sources:
        try:
            scanner.check_moc_include(source)
        except MocIncludeError as exc:
            print(f"pcons Qt: {exc}", file=sys.stderr)
            return 1
    scanner.save_cache()

    if (result.moc_headers or result.moc_sources) and not spec["has_includes"]:
        print(
            f"pcons Qt: target '{spec['target']}': moc runs with no include "
            "paths — pass the Qt modules via link=[qt.Widgets, ...] at "
            "construction so moc can resolve Qt headers (app.link() "
            "afterward is too late for moc).",
            file=sys.stderr,
        )

    def out_for(source: Path, name: str) -> Path:
        return gen_dir.joinpath(*output_rel_dir(source, project_root)) / name

    header_jobs = [
        (out_for(header, f"moc_{header.stem}.cpp"), header)
        for header in result.moc_headers
    ]
    jobs: list[tuple[Path, Path]] = [
        *header_jobs,
        *(
            (out_for(source, f"{source.stem}.moc"), source)
            for source in result.moc_sources
        ),
    ]

    moc = list(spec["moc"])
    moc_args = list(spec["moc_args"])
    floor = max(
        _mtime(spec_path),
        *(_mtime(Path(p)) for p in spec["moc_deps"]),
        0,
    )
    moc_ran = False
    for output, source in jobs:
        if _needs_moc(output, source, floor):
            code = _run_moc(moc, moc_args, output, source)
            if code != 0:
                return code
            moc_ran = True

    produced = sorted(str(output) for output, _ in jobs)
    state_path = gen_dir / "automoc.state.json"
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8")).get("outputs", [])
    except (OSError, json.JSONDecodeError):
        previous = []
    for stale in set(previous) - set(produced):
        _remove(Path(stale))

    includes = sorted(
        os.path.relpath(output, aggregator.parent).replace("\\", "/")
        for output, _ in header_jobs
    )
    body = "".join(f'#include "{name}"\n' for name in includes) or _EMPTY_TU
    _write_aggregator(aggregator, body, moc_ran)

    metatypes = spec.get("metatypes")
    if metatypes is not None:
        sidecars = [
            output.with_name(output.name + ".json")
            for output, _ in jobs
            if output.with_name(output.name + ".json").is_file()
        ]
        code = _collect_json(moc, sidecars, Path(metatypes))
        if code != 0:
            return code

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"version": SPEC_VERSION, "outputs": produced}, indent=1),
        encoding="utf-8",
    )

    deps = [str(spec_path)]
    deps += [str(p) for p in result.scanned]
    deps += [str(p) for p in result.scanned_dirs]
    for output, _ in jobs:
        deps += [str(p) for p in _read_depfile(output.with_name(output.name + ".d"))]
    _write_depfile(Path(args.depfile), aggregator, sorted(dict.fromkeys(deps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
