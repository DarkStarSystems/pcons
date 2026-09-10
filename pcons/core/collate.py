# SPDX-License-Identifier: MIT
"""Build-time collation for pcons's Scanner (discovered dependencies) primitive.

A Scanner has two halves. The *scan* half is the user's own tool: for one
governed edge it reports what that edge provides, what it requires, and any
files it discovered along the way, as a small JSON document. The *collate*
half is generic and lives here: it gathers every scan-info document of one
scope, resolves each requirement to the artifact that provides it, and writes
the Ninja dyndep file (plus optional per-edge argument files) that reorders
the build accordingly.

Collate runs from the build directory as a ninja edge::

    python -m pcons.core.collate --manifest scan/scene-refs/pack_level1.manifest.json

Three JSON schemas connect the halves. All paths in them are relative to the
build directory, and all paths emitted use forward slashes.

**Scan info** (one per governed edge, written by the user's scan tool)::

    {"version": 1,
     "provides": [{"name": "engine.core", "path": "optional/path"}],
     "requires": ["engine.render"],
     "extra_deps": ["some/discovered/file"],
     "extra_outputs": ["some/dynamic/output"]}

Every field but ``version`` is optional. A provide with no ``path`` gets the
provide template applied.

A provide names *where* a logical name lives, so that requiring edges can
depend on it; ``extra_outputs`` names what the edge writes beyond its declared
outputs, and is the only thing that becomes a dyndep implicit output. Every
provide path must therefore be backed by one of the edge's declared outputs or
by one of its ``extra_outputs`` -- a dyndep output nothing writes would leave
ninja rebuilding that edge forever, so collate rejects it.

**Manifest** (written at configure time by pcons)::

    {"version": 1, "scanner": "scene-refs", "scope": "pack_level1",
     "dyndep": "scan/scene-refs/pack_level1.dyndep",
     "exports_out": "scan/scene-refs/pack_level1.exports.json",
     "imports": ["scan/scene-refs/pack_common.exports.json"],
     "provide_template": "packs/{name}.pack",
     "on_unresolved": "ignore",
     "edge_args": {"suffix": ".modmap", "var": "SCAN_ARGS",
                   "format": {"header": ["$root ."], "line": "{name} {path}"},
                   "include": "requires+provides"},
     "edges": [{"out": "packs/level1.pack",
                "declared_outputs": ["packs/level1.pack"],
                "info": "packs/level1.pack.scaninfo.json",
                "args_file": "packs/level1.pack.modmap",
                "provide_template": null}]}

A per-edge ``provide_template`` overrides the scanner-level one when non-null.
``edge_args`` may be null or absent, meaning no argument files.

**Exports** (written here, imported by dependent scopes)::

    {"version": 1, "scanner": "scene-refs", "scope": "pack_level1",
     "provides": {"level1": "packs/level1.pack"}}

Unknown keys are ignored everywhere, so a schema can grow without breaking
older collators.
"""

from __future__ import annotations

# argparse, not click: a build-edge subprocess, where click costs ~14ms of
# import per invocation. Keep every import here light for the same reason --
# this runs once per affected edge on every build. The CLI is internal, typed
# only by pcons's own generators.
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCAN_INFO_VERSION = 1
MANIFEST_VERSION = 1
EXPORTS_VERSION = 1

ON_UNRESOLVED_MODES = ("ignore", "warn", "error")

_ERROR_PREFIX = "pcons collate:"


def write_text_if_changed(path: Path, text: str) -> None:
    """Write *text* to *path* only when content differs.

    Fast-path no-op uses a matching ``<path>.sha256`` digest file.
    If the digest file is missing or stale, use a size check first and
    only fall back to a byte-for-byte compare for equal-size candidates.
    """
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).digest()
    digest_file = path.with_suffix(path.suffix + ".sha256")

    if path.exists():
        if (
            path.stat().st_size == len(data)
            and digest_file.exists()
            and digest_file.read_bytes() == digest
        ):
            return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest_file.write_bytes(digest)


def _dyndep_escape(path: str) -> str:
    """Escape one path for ninja dyndep syntax: space, colon, and ``$``."""
    return path.replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


def write_dyndep_entries(
    entries: list[tuple[str, list[str], list[str]]],
    out_path: str | Path,
) -> None:
    """Write a Ninja dyndep file from pre-resolved (out, provides, requires).

    Each entry is ``(edge_output_rel, provides_paths, requires_paths)`` where
    the provides/requires are build-dir-relative artifact paths. Entries are
    emitted sorted by output; each entry's paths are deduped, sorted, and
    escaped — a discovered path may carry spaces (an install category, a
    spaced build tree) that would otherwise split into two paths.
    """
    lines = ["ninja_dyndep_version = 1", ""]
    for obj_rel, provides_pcms, requires_pcms in sorted(entries, key=lambda e: e[0]):
        provides = [_dyndep_escape(p) for p in sorted(set(provides_pcms))]
        requires = [_dyndep_escape(p) for p in sorted(set(requires_pcms))]
        implicit_out = " | " + " ".join(provides) if provides else ""
        implicit_in = " | " + " ".join(requires) if requires else ""
        lines.append(
            f"build {_dyndep_escape(obj_rel)}{implicit_out}: dyndep{implicit_in}"
        )
        lines.append("")

    write_text_if_changed(Path(out_path), "\n".join(lines))


def sanitize_logical_name(name: str) -> str:
    """Turn a logical name into a filename fragment.

    Mirrors the C++ module convention: ``:`` becomes ``-`` so partition names
    stay valid filenames (``jt.Math:BigUInt`` -> ``jt.Math-BigUInt``), and
    ``/`` becomes ``_`` so a hierarchical name cannot escape its directory.
    """
    return name.replace(":", "-").replace("/", "_")


class CollateError(Exception):
    """A validation failure. Reported to stderr; no output file is written."""


@dataclass
class _Provide:
    """A resolved provide: its artifact path and where the claim came from.

    ``origin`` is the providing edge for this scope's own provides, and the
    exporting scope for imported ones -- either way, what an error message
    must show to point at the source of a conflict.
    """

    path: str
    origin: str


@dataclass
class _Plan:
    """Everything collate decided to write, once every check has passed."""

    entries: list[tuple[str, list[str], list[str]]] = field(default_factory=list)
    exports: dict[str, str] = field(default_factory=dict)
    args_files: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    """Normalize an emitted path to forward slashes (ninja's separator)."""
    return str(path).replace("\\", "/")


def _read_json(build_dir: Path, rel: str, what: str) -> dict[str, Any]:
    """Read a build-dir-relative JSON object, or raise CollateError."""
    path = build_dir / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CollateError(
            f"cannot read {what} '{_norm(rel)}': {exc.strerror}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CollateError(f"{what} '{_norm(rel)}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CollateError(f"{what} '{_norm(rel)}' must be a JSON object")
    return data


def _check_version(data: dict[str, Any], expected: int, what: str) -> None:
    """Require ``data["version"] == expected``."""
    version = data.get("version")
    if version != expected:
        raise CollateError(
            f"{what} has version {version!r}, expected {expected}; "
            f"regenerate it with a matching pcons"
        )


def _load_imports(
    manifest: dict[str, Any], build_dir: Path, scope: str
) -> dict[str, _Provide]:
    """Merge every imported exports file into one name -> provide map.

    Two imports may name the same logical name only when they agree on its
    path; disagreement is a hard error, since the requiring edge could not
    tell which artifact it should depend on.
    """
    imported: dict[str, _Provide] = {}
    for rel in manifest.get("imports") or []:
        data = _read_json(build_dir, rel, f"import file (for scope '{scope}')")
        _check_version(data, EXPORTS_VERSION, f"import file '{_norm(rel)}'")
        source = str(data.get("scope") or _norm(rel))
        for name, path in (data.get("provides") or {}).items():
            path = _norm(str(path))
            existing = imported.get(name)
            if existing is not None and existing.path != path:
                raise CollateError(
                    f"imported name '{name}' has conflicting paths: "
                    f"'{existing.path}' (from scope '{existing.origin}') and "
                    f"'{path}' (from scope '{source}')"
                )
            imported[name] = _Provide(path, source)
    return imported


def _apply_template(
    template: str, name: str, scanner: str, scope: str, edge: str
) -> str:
    """Render a provide template for one logical name."""
    try:
        return _norm(
            template.format(
                name=sanitize_logical_name(name), scanner=scanner, scope=scope
            )
        )
    except (KeyError, IndexError) as exc:
        raise CollateError(
            f"edge '{edge}': provide template '{template}' uses unknown "
            f"placeholder {exc}; available are {{name}}, {{scanner}}, {{scope}}"
        ) from exc


def _load_edges(
    manifest: dict[str, Any], build_dir: Path, scanner: str, scope: str
) -> tuple[list[dict[str, Any]], dict[str, _Provide]]:
    """Read every edge's scan info and build this scope's provides map.

    Returns the per-edge records (each carrying its scan info and its own
    resolved provides) and the scope-wide name -> provide map.
    """
    default_template = manifest.get("provide_template")
    records: list[dict[str, Any]] = []
    own: dict[str, _Provide] = {}

    for edge in manifest.get("edges") or []:
        out = _norm(str(edge.get("out", "")))
        if not out:
            raise CollateError("every manifest edge needs an 'out'")
        info_rel = edge.get("info")
        if not info_rel:
            raise CollateError(f"edge '{out}' has no 'info' scan-info path")
        info = _read_json(build_dir, info_rel, f"scan info for edge '{out}'")
        _check_version(info, SCAN_INFO_VERSION, f"scan info '{_norm(info_rel)}'")

        template = edge.get("provide_template")
        if template is None:
            template = default_template

        provides: dict[str, str] = {}
        for provide in info.get("provides") or []:
            name = str(provide.get("name", ""))
            if not name:
                raise CollateError(
                    f"edge '{out}': a provide entry in "
                    f"'{_norm(str(info_rel))}' has no 'name'"
                )
            path = provide.get("path")
            if path:
                resolved = _norm(str(path))
            elif template:
                resolved = _apply_template(str(template), name, scanner, scope, out)
            else:
                raise CollateError(
                    f"edge '{out}': provide '{name}' has no path and the "
                    f"scanner has no provide_template"
                )
            previous = own.get(name)
            if previous is not None and (
                previous.origin != out or previous.path != resolved
            ):
                raise CollateError(
                    f"name '{name}' is provided twice in scope '{scope}': by "
                    f"edge '{previous.origin}' as '{previous.path}' and by "
                    f"edge '{out}' as '{resolved}'"
                )
            own[name] = _Provide(resolved, out)
            provides[name] = resolved

        records.append({"out": out, "edge": edge, "info": info, "provides": provides})

    return records, own


def _render_args_file(
    edge_args: dict[str, Any],
    provides: dict[str, str],
    requires: list[tuple[str, str]],
) -> str:
    """Render one edge's argument file from the scanner's line format."""
    fmt = edge_args.get("format") or {}
    line_fmt = str(fmt.get("line", "{name} {path}"))
    include = str(edge_args.get("include", "requires"))

    items: list[tuple[str, str]] = []
    if include == "requires+provides":
        items.extend(sorted(provides.items()))
    elif include != "requires":
        raise CollateError(
            f"edge_args.include must be 'requires' or 'requires+provides', "
            f"got {include!r}"
        )
    items.extend(sorted(requires))

    lines = [str(h) for h in (fmt.get("header") or [])]
    lines.extend(line_fmt.format(name=name, path=path) for name, path in items)
    return "".join(line + "\n" for line in lines)


def _plan(manifest: dict[str, Any], build_dir: Path) -> _Plan:
    """Validate everything and decide what to write. Raises CollateError."""
    _check_version(manifest, MANIFEST_VERSION, "manifest")
    scanner = str(manifest.get("scanner", ""))
    scope = str(manifest.get("scope", ""))
    dyndep = manifest.get("dyndep")
    if not dyndep:
        raise CollateError("manifest has no 'dyndep' output path")

    on_unresolved = str(manifest.get("on_unresolved", "ignore"))
    if on_unresolved not in ON_UNRESOLVED_MODES:
        raise CollateError(
            f"on_unresolved must be one of {', '.join(ON_UNRESOLVED_MODES)}, "
            f"got {on_unresolved!r}"
        )

    imported = _load_imports(manifest, build_dir, scope)
    records, own = _load_edges(manifest, build_dir, scanner, scope)

    # Own-scope provides shadow imported ones: a scope that builds its own
    # artifact for a name must depend on that one, not on the upstream copy.
    # An own provide at a different path than an import of the same name is
    # therefore deliberate shadowing, not a conflict.
    edge_args = manifest.get("edge_args")
    plan = _Plan()
    unresolved: list[tuple[str, str]] = []

    for record in records:
        out: str = record["out"]
        info: dict[str, Any] = record["info"]
        provides: dict[str, str] = record["provides"]
        declared = {
            _norm(str(p)) for p in (record["edge"].get("declared_outputs") or [])
        }

        resolved_requires: list[tuple[str, str]] = []
        for name in info.get("requires") or []:
            name = str(name)
            provide = own.get(name) or imported.get(name)
            if provide is None:
                if on_unresolved == "error":
                    unresolved.append((out, name))
                elif on_unresolved == "warn":
                    plan.warnings.append(
                        f"{_ERROR_PREFIX} edge '{out}' requires '{name}', "
                        f"which nothing provides"
                    )
                continue
            resolved_requires.append((name, provide.path))

        extra_outputs = [_norm(str(p)) for p in (info.get("extra_outputs") or [])]
        extra_deps = [_norm(str(p)) for p in (info.get("extra_deps") or [])]

        # A provide says where a logical name lives, never that this edge
        # writes it: only declared_outputs and extra_outputs make that claim.
        # Emitting a provide as a dyndep implicit out when nothing writes the
        # file leaves ninja with a permanently missing output, so the edge
        # reruns on every build, forever, without an error.
        written = declared | set(extra_outputs)
        for name, path in sorted(provides.items()):
            if path not in written:
                raise CollateError(
                    f"edge '{out}' provides '{name}' at '{path}', but neither "
                    f"declares that file as an output nor claims to write it "
                    f"via extra_outputs -- a dyndep output nothing writes "
                    f"makes the build rebuild forever. Point the provide at "
                    f"one of the edge's outputs, or list the file in the scan "
                    f"info's extra_outputs."
                )

        implicit_outs = sorted(set(extra_outputs) - declared)
        implicit_ins = list(
            dict.fromkeys([p for _, p in resolved_requires] + extra_deps)
        )
        plan.entries.append((out, implicit_outs, implicit_ins))

        if edge_args:
            args_file = record["edge"].get("args_file")
            if not args_file:
                raise CollateError(
                    f"edge '{out}' has no 'args_file' but the scanner "
                    f"configures edge_args"
                )
            plan.args_files.append(
                (
                    str(args_file),
                    _render_args_file(edge_args, provides, resolved_requires),
                )
            )

    if unresolved:
        listing = "\n  ".join(f"edge '{out}' requires '{n}'" for out, n in unresolved)
        raise CollateError(
            f"{len(unresolved)} unresolved requirement(s) in scope "
            f"'{scope}':\n  {listing}\n"
            f"A name is resolvable when this scope provides it or a scanned "
            f"dependency exports it — if another target provides it, this "
            f"scope's target needs a dependency on that target "
            f"(link()/depends()), which is what carries its exports "
            f"here."
        )

    plan.exports = {name: provide.path for name, provide in own.items()}
    return plan


def collate(manifest: dict[str, Any], build_dir: Path) -> int:
    """Collate one scanner scope. Returns a process exit code (0 ok, 1 error).

    Nothing is written unless every check passes, so a failed run leaves the
    previous dyndep, exports and argument files exactly as they were.
    """
    try:
        plan = _plan(manifest, build_dir)
    except CollateError as exc:
        print(f"{_ERROR_PREFIX} {exc}", file=sys.stderr)
        return 1

    for warning in plan.warnings:
        print(warning, file=sys.stderr)

    write_dyndep_entries(plan.entries, build_dir / str(manifest["dyndep"]))

    exports_out = manifest.get("exports_out")
    if exports_out:
        exports = {
            "version": EXPORTS_VERSION,
            "scanner": manifest.get("scanner", ""),
            "scope": manifest.get("scope", ""),
            "provides": plan.exports,
        }
        write_text_if_changed(
            build_dir / str(exports_out),
            json.dumps(exports, indent=1, sort_keys=True) + "\n",
        )

    for rel, text in plan.args_files:
        write_text_if_changed(build_dir / rel, text)

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Run from the build directory."""
    parser = argparse.ArgumentParser(
        prog="python -m pcons.core.collate",
        description="Collate scanner results into a Ninja dyndep file.",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="scanner manifest JSON, relative to the build directory",
    )
    args = parser.parse_args(argv)

    build_dir = Path.cwd()
    try:
        manifest = _read_json(build_dir, args.manifest, "manifest")
    except CollateError as exc:
        print(f"{_ERROR_PREFIX} {exc}", file=sys.stderr)
        return 1

    return collate(manifest, build_dir)


if __name__ == "__main__":
    sys.exit(main())
