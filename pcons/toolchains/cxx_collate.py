# SPDX-License-Identifier: MIT
"""Build-time collation for the C++20 modules scanner.

This is the C++-specific sibling of :mod:`pcons.core.collate`. The generic
collate resolves opaque logical names to artifact paths; C++ modules need
more than that. A module's compiled interface (its BMI: clang ``.pcm``, GCC
``.gcm``, MSVC ``.ifc``) is only consumable by translation units whose
BMI-sensitive flags match, so every provide and every import is keyed by a
*BMI compatibility key* computed at configure time. And a compile needs more
than ordering: it needs ``-fmodule-file=`` for each module it imports,
*transitively*, which is what the per-edge modmap response file carries.

Collate runs from the build directory as a ninja edge, once per scanned
target::

    python -m pcons.toolchains.cxx_collate --manifest scan/cxx-modules/app.manifest.json

It reads the configure-written manifest (:mod:`pcons.core.scan` writes it),
every governed edge's P1689R5 scan output, and the exports of the scanned
targets this one depends on. It writes the ninja dyndep file, this scope's
exports, and one modmap per governed edge.

**Scan info** (one per governed edge, written by ``clang-scan-deps
-format=p1689``)::

    {"version": 1,
     "rules": [{"primary-output": "obj.app/m.cppm.o",
                "provides": [{"logical-name": "M", "is-interface": true}],
                "requires": [{"logical-name": "N"}]}]}

**Manifest**: the core schema, plus C++ extras. ``extra`` carries the
toolchain style, the BMI extension and this scope's module directory; each
edge's ``extra.key`` is its BMI compatibility key::

    {"version": 1, "scanner": "cxx-modules", "scope": "app",
     "dyndep": "scan/cxx-modules/app.dyndep",
     "exports_out": "scan/cxx-modules/app.exports.json",
     "imports": ["scan/cxx-modules/lib.exports.json"],
     "link_args_file": "app.stdmods.rsp",
     "edge_args": {"suffix": ".modmap"},
     "extra": {"style": "clang", "bmi_ext": ".pcm", "moddir": "cxx_modules/app",
               "std_exports": ["scan/cxx-modules/std.0123456789ab.exports.json"],
               "std_error": "install Homebrew LLVM ..."},
     "edges": [{"out": "obj.app/m.cppm.o",
                "declared_outputs": ["obj.app/m.cppm.o"],
                "info": "obj.app/m.cppm.o.ddi",
                "args_file": "obj.app/m.cppm.o.modmap",
                "extra": {"key": "0123456789ab"}}]}

``extra.std_exports`` names configure-written exports files describing the
standard-library module (``std``, ``std.compat``) as built for each BMI key;
they are read exactly like ``imports``. ``extra.std_error`` is the
toolchain's own install hint, shown when a TU imports ``std`` and no such
file provides it. ``link_args_file``, when present, is always written: a
response file of the standard-library module objects this scope's link needs,
one path per line.

**Exports** (written here, imported by dependent scopes)::

    {"version": 1, "scanner": "cxx-modules", "scope": "app",
     "modules": {"M": {"logical": "M", "key": "0123456789ab",
                       "bmi": "cxx_modules/app/0123456789ab/M.pcm",
                       "obj": "obj.app/m.cppm.o", "is_interface": true,
                       "requires": ["N"]}},
     "std_objs": ["cxx_modules/std/0123456789ab/std.o"]}

``std_objs`` carries the standard-library module objects this scope's link
needs, so a dependent scope aggregates them transitively without rediscovering
who imported ``std``.

The map key is the logical name. One scope may compile the same module under
several BMI keys (distinct flags, distinct BMIs); the extra entries are then
keyed ``<logical>#<key>``, and every entry carries its own ``logical``, so a
reader indexes by ``(logical, key)`` and never by the map key.

All paths are relative to the build directory and use forward slashes.
Unknown keys are ignored everywhere, so these schemas can grow.
"""

from __future__ import annotations

# argparse, not click, and no heavy imports: this runs as a build edge, once
# per scanned target on every build. See pcons.core.collate for the rationale.
import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcons.core.collate import (
    MANIFEST_VERSION,
    CollateError,
    sanitize_logical_name,
    write_dyndep_entries,
    write_text_if_changed,
)

EXPORTS_VERSION = 1
P1689_VERSION = 1
SCANNER_NAME = "cxx-modules"

_ERROR_PREFIX = "pcons cxx-collate:"

# The standard library is a module like any other, except that no scope of the
# project provides it: the toolchain does, and it must, so an unresolved
# import of one of these is an error rather than a pass-through.
_STD_MODULES = frozenset({"std", "std.compat"})

_STD_UNAVAILABLE = (
    "`import std;` was used, but no standard-library module is available for "
    "this toolchain/scope.\nSee the C++20 modules section of the pcons user "
    "guide (docs/user-guide.md) for the toolchain requirements and for "
    "`env.cxx.modules = True`."
)


@dataclass(frozen=True)
class _Module:
    """One compiled module interface, in one BMI compatibility class.

    ``origin`` is the object that compiles it for this scope's own modules,
    and the exporting scope for imported ones -- either way, what an error
    message must show to point at where the interface is built.
    """

    logical: str
    key: str
    bmi: str
    obj: str
    is_interface: bool
    requires: tuple[str, ...]
    origin: str


@dataclass
class _Edge:
    """One governed compile, with its scan results resolved to modules."""

    out: str
    key: str
    args_file: str | None
    provides: list[_Module] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)


@dataclass
class _Plan:
    """Everything collate decided to write, once every check has passed."""

    entries: list[tuple[str, list[str], list[str]]] = field(default_factory=list)
    exports: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: (build-dir-relative path, content) for every modmap and response file.
    files: list[tuple[str, str]] = field(default_factory=list)
    bmi_dirs: list[str] = field(default_factory=list)
    std_objs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    """Normalize an emitted path to forward slashes (ninja's separator)."""
    return str(path).replace("\\", "/")


def bmi_path(logical_name: str, moddir: str, key: str, extension: str) -> str:
    """BMI path for a logical module in its compatibility class's directory.

    ``bmi_path("jt.Math:Big", "cxx_modules/app", "49eea", ".pcm")`` ->
    ``cxx_modules/app/49eea/jt.Math-Big.pcm``. A BMI is only consumable by
    TUs whose BMI-sensitive flags match, so each compatibility class gets its
    own directory and the same module can exist in several of them.
    """
    return f"{moddir}/{key}/{sanitize_logical_name(logical_name)}{extension}"


def _read_json(build_dir: Path, rel: str, what: str) -> dict[str, Any]:
    """Read a build-dir-relative JSON object, or raise CollateError."""
    try:
        text = (build_dir / rel).read_text(encoding="utf-8")
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


def _p1689_rules(info: dict[str, Any]) -> list[dict[str, Any]]:
    """The rules of a P1689R5 document, ignoring malformed entries."""
    rules = info.get("rules")
    if not isinstance(rules, list):
        return []
    return [rule for rule in rules if isinstance(rule, dict)]


def _p1689_provides(info: dict[str, Any]) -> list[tuple[str, bool]]:
    """Every ``(logical-name, is-interface)`` this TU provides.

    ``is-interface`` defaults to True when absent, per P1689R5; False marks an
    internal partition implementation unit.
    """
    found: list[tuple[str, bool]] = []
    for rule in _p1689_rules(info):
        entries = rule.get("provides")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("logical-name")
            if isinstance(name, str) and name:
                found.append((name, bool(entry.get("is-interface", True))))
    return found


def _p1689_requires(info: dict[str, Any]) -> list[str]:
    """Every logical name this TU imports, deduped, in scan order.

    A requirement may also carry ``source-path`` (a header unit); nothing but
    the logical name matters here.
    """
    found: dict[str, None] = {}
    for rule in _p1689_rules(info):
        entries = rule.get("requires")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("logical-name")
            if isinstance(name, str) and name:
                found[name] = None
    return list(found)


def _merge_exports(
    build_dir: Path,
    rel: str,
    scope: str,
    into: dict[tuple[str, str], _Module],
) -> dict[str, Any]:
    """Merge one exports file into *into*, returning the parsed document.

    Two sources may describe the same module in the same compatibility class
    only when they agree on its BMI path; disagreement is a hard error, since
    the importing edge could not tell which BMI it should read.
    """
    data = _read_json(build_dir, rel, f"import file (for scope '{scope}')")
    _check_version(data, EXPORTS_VERSION, f"import file '{_norm(rel)}'")
    source = str(data.get("scope") or _norm(rel))
    for map_key, entry in (data.get("modules") or {}).items():
        if not isinstance(entry, dict):
            raise CollateError(
                f"import file '{_norm(rel)}': module '{map_key}' is not a JSON object"
            )
        logical = str(entry.get("logical") or map_key)
        key = str(entry.get("key", ""))
        bmi = _norm(str(entry.get("bmi", "")))
        if not key or not bmi:
            raise CollateError(
                f"import file '{_norm(rel)}': module '{logical}' has no "
                f"'key' or no 'bmi' path"
            )
        module = _Module(
            logical=logical,
            key=key,
            bmi=bmi,
            obj=_norm(str(entry.get("obj", ""))),
            is_interface=bool(entry.get("is_interface", True)),
            requires=tuple(str(r) for r in (entry.get("requires") or [])),
            origin=source,
        )
        existing = into.get((logical, key))
        if existing is not None and existing.bmi != module.bmi:
            raise CollateError(
                f"module '{logical}' is imported from two scopes with the "
                f"same BMI-compatibility key {key}, at different paths: "
                f"'{existing.bmi}' (from scope '{existing.origin}') and "
                f"'{module.bmi}' (from scope '{module.origin}'). Build the "
                f"interface in one place."
            )
        into[(logical, key)] = module
    return data


def _load_imports(
    build_dir: Path,
    scope: str,
    import_rels: list[str],
    std_rels: list[str],
) -> tuple[dict[tuple[str, str], _Module], set[str]]:
    """Read this scope's imports into one ``(logical, key)`` map.

    Returns that map and the standard-library module objects the imported
    scopes already need at link time -- those travel with the exports so a
    scope inherits them without rediscovering who imported ``std``.

    The standard library's own exports are configure-written and read the
    same way, but they describe the toolchain rather than a dependency, so
    they contribute no link objects of their own here.
    """
    imported: dict[tuple[str, str], _Module] = {}
    std_objs: set[str] = set()
    for rel in import_rels:
        data = _merge_exports(build_dir, str(rel), scope, imported)
        std_objs.update(_norm(str(obj)) for obj in (data.get("std_objs") or []))
    for rel in std_rels:
        _merge_exports(build_dir, str(rel), scope, imported)
    return imported, std_objs


def _load_edges(
    manifest: dict[str, Any],
    build_dir: Path,
    moddir: str,
    bmi_ext: str,
) -> tuple[list[_Edge], dict[tuple[str, str], _Module]]:
    """Read every edge's scan output and build this scope's provider map.

    Two edges providing the same module in the same compatibility class would
    write the same BMI file, so that is rejected here. The same module under
    *different* keys is fine: those are distinct BMIs.
    """
    edges: list[_Edge] = []
    own: dict[tuple[str, str], _Module] = {}

    for spec in manifest.get("edges") or []:
        out = _norm(str(spec.get("out", "")))
        if not out:
            raise CollateError("every manifest edge needs an 'out'")
        info_rel = spec.get("info")
        if not info_rel:
            raise CollateError(f"edge '{out}' has no 'info' scan-output path")
        key = str((spec.get("extra") or {}).get("key", ""))
        if not key:
            raise CollateError(
                f"edge '{out}' has no 'extra.key' BMI-compatibility key; "
                f"regenerate the build files with a matching pcons"
            )

        info = _read_json(build_dir, info_rel, f"scan output for edge '{out}'")
        # The P1689 "version" is the format's own revision counter, not
        # pcons's: clang writes 1, GCC writes 0. Both carry the same rules.
        if info.get("version") not in (0, P1689_VERSION):
            _check_version(info, P1689_VERSION, f"scan output '{_norm(str(info_rel))}'")

        edge = _Edge(
            out=out,
            key=key,
            args_file=(
                _norm(str(spec["args_file"])) if spec.get("args_file") else None
            ),
            requires=_p1689_requires(info),
        )
        for logical, is_interface in _p1689_provides(info):
            module = _Module(
                logical=logical,
                key=key,
                bmi=bmi_path(logical, moddir, key, bmi_ext),
                obj=out,
                is_interface=is_interface,
                requires=tuple(edge.requires),
                origin=out,
            )
            previous = own.get((logical, key))
            if previous is not None and previous.obj == out:
                continue  # the same TU listed the module twice
            if previous is not None:
                raise CollateError(
                    f"module '{logical}' is compiled into two different "
                    f"objects ({previous.obj} and {out}) with BMI-equivalent "
                    f"flags, so both would write the same {module.bmi}. Give "
                    f"them distinct BMI-sensitive flags or build the interface "
                    f"in one place."
                )
            own[(logical, key)] = module
            edge.provides.append(module)
        edges.append(edge)

    return edges, own


class _Resolver:
    """Resolves logical names to modules within one BMI compatibility class."""

    def __init__(
        self,
        own: dict[tuple[str, str], _Module],
        imported: dict[tuple[str, str], _Module],
        scope: str,
        std_error: str | None = None,
    ) -> None:
        # Own provides shadow imported ones: a scope that compiles its own
        # interface must read that BMI, not an upstream copy.
        self._own = own
        self._imported = imported
        self._scope = scope
        self._std_error = std_error
        #: Unresolved-import notices, for the caller to report.
        self.warnings: list[str] = []

    def find(self, logical: str, key: str) -> _Module | None:
        """The module providing *logical* for compatibility class *key*."""
        return self._own.get((logical, key)) or self._imported.get((logical, key))

    def _other_keys(self, logical: str) -> list[_Module]:
        """Every known provider of *logical*, in any compatibility class."""
        return [
            module
            for source in (self._own, self._imported)
            for (name, _), module in source.items()
            if name == logical
        ]

    def resolve_direct(self, edge: _Edge) -> list[_Module]:
        """Resolve an edge's imports, rejecting key mismatches.

        A name provided only in *other* compatibility classes could never
        satisfy this import, and the compiler's own error would be far less
        clear. ``std`` and ``std.compat`` are an error when nothing provides
        them at all: the toolchain owes the project a standard-library module,
        and the compiler alone would fail much further downstream. Any other
        name nothing provides only warns and passes through -- it may be
        satisfied externally (a header unit, or a prebuilt BMI the user points
        the compiler at) -- but far more often the providing target is simply
        not a dependency of this one, and the compiler's "module not found"
        says nothing about that.
        """
        resolved: list[_Module] = []
        for logical in edge.requires:
            module = self.find(logical, edge.key)
            if module is not None:
                resolved.append(module)
                continue
            elsewhere = self._other_keys(logical)
            if elsewhere:
                others = ", ".join(sorted({m.obj or m.origin for m in elsewhere}))
                raise CollateError(
                    f"module '{logical}' is imported by {edge.out}, but its "
                    f"compiled interface is only built with different "
                    f"BMI-sensitive flags (by {others}). A module interface is "
                    f"only consumable by TUs whose BMI-sensitive flags (C++ "
                    f"dialect, ABI options) match. Compile the interface with "
                    f"this TU's flags too (e.g. add its source to the "
                    f"importing target), or align the targets' flags."
                )
            if logical in _STD_MODULES:
                raise CollateError(self._std_error or _STD_UNAVAILABLE)
            self.warnings.append(
                f"{_ERROR_PREFIX} {edge.out} imports module '{logical}', which "
                f"nothing in scope '{self._scope}' or its dependencies "
                f"provides. If another target provides it, this target needs "
                f"link() or depends() on that target, which is what carries "
                f"its module exports here; a header unit or a module from "
                f"outside the build is fine."
            )
        return resolved

    def closure(self, direct: list[_Module], key: str) -> list[_Module]:
        """*direct* plus everything those modules transitively require.

        A compile needs ``-fmodule-file=`` for every module reachable from its
        imports, not just the ones it names. Unresolvable transitive names are
        skipped without a word: they are some other TU's direct imports, and
        the scope that compiles that TU is the one that warns about them (see
        :meth:`resolve_direct`). Revisits are cheap cycle protection.
        """
        seen: dict[str, _Module] = {}
        queue = list(direct)
        while queue:
            module = queue.pop()
            if module.logical in seen:
                continue
            seen[module.logical] = module
            for logical in module.requires:
                if logical in seen:
                    continue
                required = self.find(logical, key)
                if required is not None:
                    queue.append(required)
        return sorted(seen.values(), key=lambda m: m.logical)


def _rsp_quote(arg: str) -> str:
    """Quote one argument for a compiler response file, if it needs it."""
    if not any(c.isspace() or c in "\"'" for c in arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _clang_modmap(edge: _Edge, modules: list[_Module]) -> str:
    """Render one edge's clang modmap response file.

    A providing TU is compiled as a module interface and told where to write
    its BMI; every TU is told where to find the BMI of each module it imports,
    directly or transitively. Response files split on whitespace, so ``-x
    c++-module`` is one line.
    """
    lines: list[str] = []
    if edge.provides:
        lines.append("-x c++-module")
        for module in sorted(edge.provides, key=lambda m: m.logical):
            lines.append(_rsp_quote(f"-fmodule-output={module.bmi}"))
    for module in modules:
        lines.append(_rsp_quote(f"-fmodule-file={module.logical}={module.bmi}"))
    return "".join(line + "\n" for line in lines)


def _gcc_modmap(edge: _Edge, modules: list[_Module]) -> str:
    """Render one edge's GCC module mapper file (libcody dialect).

    One ``<logical> <path>`` line per module — both what this TU provides
    (GCC writes the BMI where the mapper says, there is no
    ``-fmodule-output``) and everything it imports, directly or
    transitively. ``$root .`` anchors relative paths at the build dir.
    """
    lines: list[str] = ["$root ."]
    for module in sorted(edge.provides, key=lambda m: m.logical):
        lines.append(f"{module.logical} {module.bmi}")
    for module in modules:
        lines.append(f"{module.logical} {module.bmi}")
    return "".join(line + "\n" for line in lines)


def _msvc_modmap(edge: _Edge, modules: list[_Module]) -> str:
    """Render one edge's MSVC modmap response file.

    A providing TU is compiled as an interface unit (or an internal
    partition — the two flags are mutually exclusive, D8016) and told where
    to write its IFC; every TU gets an explicit ``/reference name=path`` per
    transitively imported module, replacing ``/ifcSearchDir``.
    """
    # cl.exe does not join a flag on one response-file line with an
    # argument on the next (D8004), so a flag and its argument share a
    # line, with only the argument quoted when it needs to be.
    lines: list[str] = []
    if edge.provides:
        interface = any(m.is_interface for m in edge.provides)
        lines.append("/interface" if interface else "/internalPartition")
        for module in sorted(edge.provides, key=lambda m: m.logical):
            lines.append(f"/ifcOutput {_rsp_quote(module.bmi)}")
    for module in modules:
        lines.append(f"/reference {_rsp_quote(f'{module.logical}={module.bmi}')}")
    return "".join(line + "\n" for line in lines)


_MODMAP_STYLES = {
    "clang": _clang_modmap,
    "gcc": _gcc_modmap,
    "msvc": _msvc_modmap,
}


def _plan(manifest: dict[str, Any], build_dir: Path) -> _Plan:
    """Validate everything and decide what to write. Raises CollateError."""
    _check_version(manifest, MANIFEST_VERSION, "manifest")
    scope = str(manifest.get("scope", ""))
    if not manifest.get("dyndep"):
        raise CollateError("manifest has no 'dyndep' output path")

    extra = manifest.get("extra") or {}
    style = str(extra.get("style", ""))
    modmap_for = _MODMAP_STYLES.get(style)
    if modmap_for is None:
        known = ", ".join(sorted(_MODMAP_STYLES))
        raise CollateError(
            f"module style '{style}' is not implemented yet; this collate knows {known}"
        )
    moddir = _norm(str(extra.get("moddir", "")))
    bmi_ext = str(extra.get("bmi_ext", ""))
    if not moddir or not bmi_ext:
        raise CollateError(
            "manifest 'extra' needs both a 'moddir' and a 'bmi_ext'; "
            "regenerate the build files with a matching pcons"
        )

    std_error = extra.get("std_error")
    imported, std_objs = _load_imports(
        build_dir,
        scope,
        list(manifest.get("imports") or []),
        list(extra.get("std_exports") or []),
    )
    edges, own = _load_edges(manifest, build_dir, moddir, bmi_ext)
    resolver = _Resolver(own, imported, scope, str(std_error) if std_error else None)
    wants_args = bool(manifest.get("edge_args"))

    plan = _Plan()
    link_objs = set(std_objs)
    for edge in edges:
        direct = resolver.resolve_direct(edge)
        closure = resolver.closure(direct, edge.key)
        provides = [module.bmi for module in edge.provides]
        # Direct imports alone order the build: a BMI cannot exist before
        # everything it was compiled against, so the transitive closure is
        # already ordered through the chain.
        plan.entries.append((edge.out, provides, [m.bmi for m in direct]))
        plan.bmi_dirs.extend(provides)
        plan.bmi_dirs.extend(m.bmi for m in direct)
        # A standard-library module is compiled here like any other, so its
        # object has to reach the link of every scope that reaches the module.
        link_objs.update(m.obj for m in closure if m.logical in _STD_MODULES and m.obj)

        if wants_args:
            if edge.args_file is None:
                raise CollateError(
                    f"edge '{edge.out}' has no 'args_file' but the scanner "
                    f"configures edge_args"
                )
            plan.files.append((edge.args_file, modmap_for(edge, closure)))

    plan.warnings = resolver.warnings
    plan.std_objs = sorted(link_objs)
    link_args_file = manifest.get("link_args_file")
    if link_args_file:
        plan.files.append(
            (
                _norm(str(link_args_file)),
                "".join(obj + "\n" for obj in plan.std_objs),
            )
        )

    for (logical, key), module in sorted(own.items()):
        map_key = logical if logical not in plan.exports else f"{logical}#{key}"
        plan.exports[map_key] = {
            "logical": logical,
            "key": key,
            "bmi": module.bmi,
            "obj": module.obj,
            "is_interface": module.is_interface,
            "requires": list(module.requires),
        }
    return plan


def collate(manifest: dict[str, Any], build_dir: Path) -> int:
    """Collate one C++ modules scope. Returns an exit code (0 ok, 1 error).

    Nothing is written unless every check passes, so a failed run leaves the
    previous dyndep, exports and modmaps exactly as they were.
    """
    try:
        plan = _plan(manifest, build_dir)
    except CollateError as exc:
        print(f"{_ERROR_PREFIX} {exc}", file=sys.stderr)
        return 1

    for warning in plan.warnings:
        print(warning, file=sys.stderr)

    # Ninja creates the directories of an edge's declared outputs, but a BMI
    # is a dyndep-discovered implicit output; nobody else would make its
    # directory before the compiler tries to write there.
    for bmi in sorted(set(plan.bmi_dirs)):
        (build_dir / bmi).parent.mkdir(parents=True, exist_ok=True)

    write_dyndep_entries(plan.entries, build_dir / str(manifest["dyndep"]))

    exports_out = manifest.get("exports_out")
    if exports_out:
        exports = {
            "version": EXPORTS_VERSION,
            "scanner": manifest.get("scanner", SCANNER_NAME),
            "scope": manifest.get("scope", ""),
            "modules": plan.exports,
            "std_objs": plan.std_objs,
        }
        write_text_if_changed(
            build_dir / str(exports_out),
            json.dumps(exports, indent=1, sort_keys=True) + "\n",
        )

    for rel, text in plan.files:
        write_text_if_changed(build_dir / rel, text)

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Run from the build directory."""
    parser = argparse.ArgumentParser(
        prog="python -m pcons.toolchains.cxx_collate",
        description="Collate C++ module scan results into a Ninja dyndep file.",
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
