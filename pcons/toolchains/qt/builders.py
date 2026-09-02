# SPDX-License-Identifier: MIT
"""High-level Qt target builders: QtProgram, QtSharedLibrary,
QtStaticLibrary, and QtResources.

The Qt wrappers accept ``.ui`` and ``.qrc`` files directly in sources and
run automoc — a scan of the target's sources and their project-local
headers for Q_OBJECT/Q_GADGET/Q_NAMESPACE:

    qt = find_qt(project, env, modules=["Widgets"])
    app = project.QtProgram("myapp", env,
        sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
        link=[qt.Widgets])

What this automates, per file kind:

- header with Q_OBJECT  -> moc -> moc_*.cpp, #included by mocs_compilation.cpp
- .cpp with Q_OBJECT    -> moc -> *.moc; the .cpp must #include it
  (hard error at build time if it doesn't)
- .ui                   -> uic edge -> ui_*.h; its dir joins the include
  path of every TU
- .qrc                  -> rcc edge -> qrc_*.cpp compiled and linked

Everything lands under ``build/<declaring-subdir>/qt.<target>/``, beside
the target's own object directory. Which files need moc is a
fact about their content, so one build-time edge per target does the scan
and runs moc, and its single static output — ``mocs_compilation.cpp``,
the CMake AUTOMOC shape — is what the target compiles. A generated
Q_OBJECT header is then an ordinary input of that edge: order it with
``app.depends(generator)`` and it joins the moc set on the first build.

QtResources synthesizes the .qrc from a Python file list (no XML):

    res = project.QtResources("assets", env, files=["images/*.png"], prefix="/")
    app.link(res)
"""

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from pcons.core.builder_registry import builder
from pcons.core.node import FileNode, Node
from pcons.core.subst import PathToken
from pcons.toolchains.qt.scan import _HEADER_SUFFIXES, output_rel_dir
from pcons.toolchains.qt.toolchain import (
    MOC_SOURCE_SUFFIXES,
    _source_path,
    _source_rel_dir,
)
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation

_CPP_SUFFIXES = MOC_SOURCE_SUFFIXES + (".c", ".m", ".mm")


def _require_qt_tool(env: Environment, what: str) -> None:
    if not env.has_tool("qt"):
        raise RuntimeError(
            f"{what} needs the qt toolchain on the environment. "
            f'Call find_qt(project, env, modules=[...]) first (or env.add_toolchain("qt")).'
        )


def _write_if_changed(path: Path, content: str) -> None:
    """Write a generate-time file only when its content changed.

    Keeps the file's mtime stable across regenerations so downstream
    build edges (rcc, automoc) don't re-run needlessly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")


def _qrc_xml(prefix: str, entries: Sequence[tuple[str, Path]]) -> str:
    """A .qrc document embedding *entries* as (alias, absolute path).

    The single home of qrc synthesis; aliases and paths are XML-escaped
    (filenames containing ``&`` are legal and must survive).
    """
    lines = ["<RCC>", f'    <qresource prefix="{escape(prefix)}">']
    for alias, path in entries:
        lines.append(
            f'        <file alias="{escape(alias)}">{escape(str(path))}</file>'
        )
    lines += ["    </qresource>", "</RCC>", ""]
    return "\n".join(lines)


def _stamped_command(env: Environment, *command: str) -> list[str]:
    """A build command wrapped in the run-then-touch-stamp helper.

    For tools with no declarable output of their own (lupdate,
    macdeployqt, windeployqt); $TARGET is the stamp file.
    """
    return [
        str(env.qt.python),
        "-m",
        "pcons.toolchains.qt._stamped",
        "--stamp",
        "$TARGET",
        "--",
        *command,
    ]


def _qt_gen_dir_for(
    project: Project, env: Environment, subdir: str
) -> tuple[Path, Path]:
    """Where a Qt builder writes its generated files: ``(root, gen_dir)``.

    *gen_dir* is build-relative and carries the declaring project's offset
    from the top-level root, so two subdirectories declaring a target of one
    name do not write over each other's generated files.

    *root* is the top-level project's root directory, the one node paths are
    anchored at. A sub-project's own ``root_dir`` names a directory the
    generated build files never refer to, so a file written there is a file
    no rule knows how to make.
    """
    root = project._path_resolver.project_root
    return root, env.build_dir_for(project._node_offset) / subdir


def _env_include_dirs(project: Project, env: Environment) -> list[Path]:
    """The environment's compiler include dirs, anchored at project root."""
    dirs: list[Path] = []
    for tool in ("cxx", "cc"):
        if env.has_tool(tool):
            for entry in getattr(env, tool).get("includes", []) or []:
                path = Path(str(entry))
                if not path.is_absolute():
                    path = project.root_dir / path
                dirs.append(path)
    return dirs


def _env_defines(env: Environment) -> list[str]:
    """The environment's compiler defines."""
    defines: list[str] = []
    for tool in ("cxx", "cc"):
        if env.has_tool(tool):
            defines.extend(str(d) for d in getattr(env, tool).get("defines", []) or [])
    return defines


def _moc_inputs(
    project: Project, env: Environment, link: Sequence[Target]
) -> tuple[list[str], list[str], list[str]]:
    """(includes, defines, extra flags) moc must see for this target.

    moc does not inherit the compiler's flags: it needs every include
    dir and define the compile would get — the environment's own plus
    the link closure's public requirements — or platform/feature-gated
    Q_OBJECT declarations are silently mis-parsed (the classic AUTOMOC
    failure this feature exists to avoid). macOS framework flags (-F...)
    pass through as-is since moc understands -F.
    """
    includes: list[str] = [str(p) for p in _env_include_dirs(project, env)]
    defines: list[str] = _env_defines(env)
    flags: list[str] = []
    seen: set[int] = set()

    def add_target(target: Target) -> None:
        if id(target) in seen:
            return
        seen.add(id(target))
        for inc in target.public.include_dirs:
            includes.append(str(inc))
        for define in target.public.defines:
            defines.append(str(define))
        for flag in target.public.compile_flags:
            if str(flag).startswith("-F"):
                flags.append(str(flag))
        for dep in target.public.link_libs:
            if not isinstance(dep, str):
                add_target(dep)

    for target in link:
        add_target(target)
    return (
        list(dict.fromkeys(includes)),
        list(dict.fromkeys(defines)),
        list(dict.fromkeys(flags)),
    )


def _scan_include_dirs(
    project: Project, env: Environment, link: Sequence[Target]
) -> list[Path]:
    """Include dirs the automoc scanner resolves #includes against.

    The environment's own include dirs (which may point outside the
    project — sibling repos, vendored code) plus the public include
    dirs of *project-built* targets in the link closure: a linked
    library's headers are exactly where Q_OBJECT classes live in
    multi-library layouts. Imported packages (Qt modules, pkg-config
    finds) are excluded — their headers are prebuilt and must never
    grow moc edges here.
    """
    from pcons.toolchains.qt.finder import qt_install

    qt = qt_install(project, env)
    dirs = [
        d
        for d in _env_include_dirs(project, env)
        # env.use(qt.Module) copies Qt's include dirs into the env;
        # never treat the Qt install itself as scannable project code.
        if qt is None or not d.is_relative_to(qt.prefix)
    ]
    seen: set[int] = set()

    def add_target(target: Target) -> None:
        if id(target) in seen:
            return
        seen.add(id(target))
        if not getattr(target, "is_imported", False):
            for inc in target.public.include_dirs:
                path = Path(str(inc))
                if not path.is_absolute():
                    path = project.root_dir / path
                dirs.append(path)
        for dep in target.public.link_libs:
            if not isinstance(dep, str):
                add_target(dep)

    for target in link:
        add_target(target)
    return list(dict.fromkeys(dirs))


_automoc_edges: weakref.WeakKeyDictionary[Project, list[tuple[FileNode, Target]]] = (
    weakref.WeakKeyDictionary()
)


def inherit_automoc_deps(project: Project) -> None:
    """Order each automoc edge behind whatever its target's compiles wait on.

    Called from the Qt toolchain's ``after_resolve`` hook, once the compiles
    exist and ``target.depends()`` has been applied to them. The automoc edge
    reads what those compiles read, so a generated Q_OBJECT header reaches it
    through the node graph rather than through an existence check. The pending
    edges are consumed here, so a second toolchain instance's hook is a no-op.

    The registry is keyed by the top-level project, because that is the one
    the hook is handed: a Qt target declared by an ``add_subdirectory``
    child registers against the child project, and keying by it would leave
    that edge unordered.

    The automoc edge itself is never inherited: it is a source of the target
    and so an input of its own compile, which would read back here as a
    self-cycle.
    """
    for node, target in _automoc_edges.pop(project.top, []):
        sources = target.intermediate_nodes or target.output_nodes
        inherited = [
            dep
            for source in sources
            for dep in (*source.implicit_deps, *source.order_only_deps)
            if isinstance(dep, FileNode) and dep is not node
        ]
        if inherited:
            node.wait_for(inherited)


def _set_node_vars(node: Node, node_vars: dict[str, object]) -> None:
    """Attach per-edge command variables (the swift.py precedent)."""
    info = getattr(node, "_build_info", None)
    if info is not None:
        info["vars"] = node_vars


def _declare_implicit_output(primary: FileNode, extra: FileNode) -> None:
    """Make *extra* a second, implicit output of *primary*'s edge.

    Ninja's ``$out`` covers the explicit outputs only, so the edge keeps its
    ``$out.d`` depfile while still telling the build about the file it writes
    on the side.
    """
    info = getattr(primary, "_build_info", None)
    if info is None:
        return
    info["outputs"] = {
        "primary": {
            "path": primary.path,
            "suffix": primary.path.suffix,
            "implicit": False,
            "required": True,
        },
        "extra": {
            "path": extra.path,
            "suffix": extra.path.suffix,
            "implicit": True,
            "required": True,
        },
    }
    extra._build_info = {"primary_node": primary, "output_name": "extra"}


def _moc_args(qt_env: Environment, predefs: Path | None) -> list[str]:
    """The moc command line the automoc tool runs with, minus in and out.

    The same includes, defines and flags ``$qt.moccmd`` would expand to;
    ``$qt.mocpredefs`` becomes a plain path because the tool, unlike a ninja
    rule, has no build-relative reference to resolve it against.
    """
    dprefix = str(qt_env.qt.get("dprefix", "-D"))
    iprefix = str(qt_env.qt.get("iprefix", "-I"))
    args = [str(flag) for flag in qt_env.qt.mocflags]
    args += [f"{dprefix}{define}" for define in qt_env.qt.mocdefines]
    args += [f"{iprefix}{include}" for include in qt_env.qt.mocincludes]
    if predefs is not None:
        args += ["--include", str(predefs)]
    return args


def _dot_moc_dirs(cpp_paths: Sequence[Path], qt_dir: Path, root: Path) -> list[Path]:
    """Directories a self-mocing source's ``<stem>.moc`` may land in.

    Which sources need one is content, decided while the build runs, but
    *where* the file goes follows from the static source list — so the
    include path a ``#include "x.moc"`` needs stays a generate-time fact.
    """
    return [
        qt_dir.joinpath(*output_rel_dir(path.resolve(), root))
        for path in cpp_paths
        if path.suffix in MOC_SOURCE_SUFFIXES
    ]


@dataclass
class _QtGenInfo:
    """Internal: what _qt_make_target generated (consumed by QtQmlModule)."""

    qt_env: Environment
    qt_dir: Path  # build-relative, e.g. build/qt.<name>
    automoc_node: Node | None = None
    metatypes_node: Node | None = None
    moc_header_dirs: list[Path] = field(default_factory=list)


def _qt_make_target(
    kind: str,
    project: Project,
    name: str,
    env: Environment,
    sources: Sequence[str | Path | Node | Target] | None,
    *,
    link: Sequence[Target] = (),
    automoc: bool = True,
    autouic: bool = True,
    autorcc: bool = True,
    no_moc: Sequence[str | Path] = (),
    moc_json: bool = False,
    defined_at: SourceLocation | None = None,
) -> tuple[Target, _QtGenInfo]:
    """Shared implementation of QtProgram/QtSharedLibrary/QtStaticLibrary."""
    _require_qt_tool(env, f"Qt{kind}()")
    defined_at = defined_at or get_caller_location()
    root, qt_dir = _qt_gen_dir_for(project, env, f"qt.{name}")
    build_dir = qt_dir.parent
    qt_env = env.clone()

    # ---- partition sources ----------------------------------------------
    plain: list[str | Path | Node | Target] = []
    cpp_paths: list[Path] = []  # project files eligible for the moc scan
    ui_files: list[Path] = []
    qrc_files: list[Path] = []

    def entry_path(entry: str | Path | FileNode) -> Path:
        path = _source_path(entry) if isinstance(entry, FileNode) else Path(entry)
        return path if path.is_absolute() else project.root_dir / path

    for entry in sources or []:
        suffix = None
        if isinstance(entry, (str, Path)):
            suffix = Path(entry).suffix
        elif isinstance(entry, FileNode):
            suffix = entry.path.suffix
        if suffix == ".ui" and autouic and isinstance(entry, (str, Path, FileNode)):
            ui_files.append(
                _source_path(entry) if isinstance(entry, FileNode) else Path(entry)
            )
            continue
        if suffix == ".qrc" and autorcc and isinstance(entry, (str, Path, FileNode)):
            qrc_files.append(
                _source_path(entry) if isinstance(entry, FileNode) else Path(entry)
            )
            continue
        if suffix in _HEADER_SUFFIXES and isinstance(entry, (str, Path, FileNode)):
            # Headers listed in sources (the CMake/qmake convention):
            # scan them for moc, but never hand them to the compiler or
            # linker — a bare .h on a link line is a baffling error.
            cpp_paths.append(entry_path(entry))
            continue
        plain.append(entry)
        if suffix in _CPP_SUFFIXES and isinstance(entry, (str, Path, FileNode)):
            path = entry_path(entry)
            # Generated sources (under the build dir) are not scanned.
            try:
                path.relative_to(root / build_dir)
            except ValueError:
                cpp_paths.append(path)

    # ---- moc environment -------------------------------------------------
    includes, defines, extra_flags = _moc_inputs(project, env, link)
    qt_env.qt.mocincludes = list(includes)
    qt_env.qt.mocdefines = list(defines)
    if moc_json:
        # QML type registration consumes moc's JSON sidecar output.
        extra_flags = [*extra_flags, "--output-json"]
    if extra_flags:
        qt_env.qt.mocflags = list(qt_env.qt.mocflags) + extra_flags

    # Compiler predefines for moc (GCC/Clang; MSVC uses --compiler-flavor).
    predefs_node: Node | None = None
    predefs_path: Path | None = None
    toolchain_name = env.toolchain.name if env.toolchain is not None else ""
    if toolchain_name in ("msvc", "clang-cl"):
        qt_env.qt.mocflags = list(qt_env.qt.mocflags) + ["--compiler-flavor", "msvc"]
    elif env.has_tool("cxx"):
        predefs_node = qt_env.qt.Predefs(qt_dir / "moc_predefs.h")[0]
        predefs_path = root / qt_dir / "moc_predefs.h"
        qt_env.qt.mocpredefs = [
            "--include",
            PathToken(
                path=project._path_resolver.make_execution_relative(
                    qt_dir / "moc_predefs.h"
                ),
                path_type="build",
            ),
        ]

    # ---- uic / rcc edges -------------------------------------------------
    gen_header_dirs: list[Path] = [qt_dir]
    ui_nodes: list[Node] = []
    for ui in ui_files:
        rel = _source_rel_dir(qt_env, project.node(ui))
        target_path = qt_dir.joinpath(*rel) / f"ui_{ui.stem}.h"
        ui_nodes.append(qt_env.qt.Uic(target_path, str(ui))[0])
        gen_header_dirs.append(target_path.parent)

    qrc_nodes: list[Node] = []
    for qrc in qrc_files:
        rel = _source_rel_dir(qt_env, project.node(qrc))
        target_path = qt_dir.joinpath(*rel) / f"qrc_{qrc.stem}.cpp"
        qrc_nodes.append(qt_env.qt.Rcc(target_path, str(qrc))[0])

    # ---- automoc ---------------------------------------------------------
    automoc_node: Node | None = None
    metatypes_node: Node | None = None
    moc_header_dirs: list[Path] = []
    if automoc and cpp_paths:
        metatypes_rel = qt_dir / f"{name}_metatypes.json" if moc_json else None
        spec_rel = qt_dir / "automoc.json"
        _write_if_changed(
            root / spec_rel,
            json.dumps(
                {
                    "version": 1,
                    "target": name,
                    "project_root": str(root),
                    "gen_dir": str(root / qt_dir),
                    "sources": sorted(str(p) for p in cpp_paths),
                    "include_dirs": [
                        str(p) for p in _scan_include_dirs(project, env, link)
                    ],
                    "no_moc": sorted(str(project.root_dir / p) for p in no_moc),
                    "moc": [str(qt_env.qt.moc)],
                    "moc_args": _moc_args(qt_env, predefs_path),
                    "moc_deps": [str(predefs_path)] if predefs_path else [],
                    "has_includes": bool(includes),
                    "metatypes": (
                        None if metatypes_rel is None else str(root / metatypes_rel)
                    ),
                },
                indent=1,
                sort_keys=True,
            ),
        )
        edge = qt_env.qt.Automoc(
            qt_dir / "mocs_compilation.cpp", [spec_rel, *cpp_paths]
        )[0]
        _set_node_vars(
            edge,
            {
                "AUTOMOCSPEC": PathToken(
                    path=project._path_resolver.make_execution_relative(spec_rel),
                    path_type="build",
                )
            },
        )
        if predefs_node is not None:
            edge.implicit_deps.append(predefs_node)
        if metatypes_rel is not None:
            metatypes_node = project.node(metatypes_rel)
            _declare_implicit_output(edge, metatypes_node)
        automoc_node = edge

        gen_header_dirs.extend(_dot_moc_dirs(cpp_paths, qt_dir, root))
        moc_header_dirs = [p.parent for p in cpp_paths]

    # ---- the real target -------------------------------------------------
    factory = getattr(project, kind)
    target: Target = factory(
        name,
        env,
        sources=[*plain, *([automoc_node] if automoc_node else []), *qrc_nodes],
        defined_at=defined_at,
    )
    if link:
        target.link(*link)
    for directory in dict.fromkeys(gen_header_dirs):
        target.private.include_dirs.append(directory)
    for node in (predefs_node, automoc_node, *ui_nodes):
        if node is not None:
            target.depends(node)
    if isinstance(automoc_node, FileNode):
        _automoc_edges.setdefault(project.top, []).append((automoc_node, target))

    info = _QtGenInfo(
        qt_env=qt_env,
        qt_dir=qt_dir,
        automoc_node=automoc_node,
        metatypes_node=metatypes_node,
        moc_header_dirs=list(dict.fromkeys(moc_header_dirs)),
    )
    return target, info


@builder(
    "QtProgram",
    target_type="program",
    requires_env=True,
    description="Qt program with automoc/autouic/autorcc",
)
class QtProgramBuilder:
    """A Program whose sources may include .ui/.qrc files and Q_OBJECT
    classes; all Qt code generation is wired automatically."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: Sequence[str | Path | Node] | None = None,
        *,
        link: Sequence[Target] = (),
        automoc: bool = True,
        autouic: bool = True,
        autorcc: bool = True,
        no_moc: Sequence[str | Path] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a Qt program target.

        Args:
            project: The project.
            name: Target name.
            env: Environment; must have the qt toolchain (via find_qt).
            sources: C++ sources, headers implied, plus .ui and .qrc files.
            link: Targets to link — pass the Qt modules here
                (link=[qt.Widgets]) so moc sees their headers/defines.
            automoc/autouic/autorcc: Disable individual generators.
            no_moc: Files to exclude from the moc scan.
        """
        target, _ = _qt_make_target(
            "Program",
            project,
            name,
            env,
            sources,
            link=link,
            automoc=automoc,
            autouic=autouic,
            autorcc=autorcc,
            no_moc=no_moc,
            defined_at=defined_at or get_caller_location(),
        )
        return target


@builder(
    "QtSharedLibrary",
    target_type="shared_library",
    requires_env=True,
    description="Qt shared library with automoc/autouic/autorcc",
)
class QtSharedLibraryBuilder:
    """SharedLibrary variant of QtProgram."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: Sequence[str | Path | Node] | None = None,
        *,
        link: Sequence[Target] = (),
        automoc: bool = True,
        autouic: bool = True,
        autorcc: bool = True,
        no_moc: Sequence[str | Path] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        target, _ = _qt_make_target(
            "SharedLibrary",
            project,
            name,
            env,
            sources,
            link=link,
            automoc=automoc,
            autouic=autouic,
            autorcc=autorcc,
            no_moc=no_moc,
            defined_at=defined_at or get_caller_location(),
        )
        return target


@builder(
    "QtStaticLibrary",
    target_type="static_library",
    requires_env=True,
    description="Qt static library with automoc/autouic/autorcc",
)
class QtStaticLibraryBuilder:
    """StaticLibrary variant of QtProgram.

    Note: resources compiled into a static library need
    Q_INIT_RESOURCE(<name>) called from the consuming application, or
    the linker may drop the auto-registration object.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: Sequence[str | Path | Node] | None = None,
        *,
        link: Sequence[Target] = (),
        automoc: bool = True,
        autouic: bool = True,
        autorcc: bool = True,
        no_moc: Sequence[str | Path] = (),
        defined_at: SourceLocation | None = None,
    ) -> Target:
        target, _ = _qt_make_target(
            "StaticLibrary",
            project,
            name,
            env,
            sources,
            link=link,
            automoc=automoc,
            autouic=autouic,
            autorcc=autorcc,
            no_moc=no_moc,
            defined_at=defined_at or get_caller_location(),
        )
        return target


@builder(
    "QtResources",
    target_type="object",
    requires_env=True,
    description="Qt resources from a file list (no .qrc XML)",
)
class QtResourcesBuilder:
    """Embed files as Qt resources without writing .qrc XML.

    Synthesizes the .qrc at generate time, runs rcc (with a depfile, so
    editing any listed file re-embeds it), and compiles the result into
    an object target to link:

        res = project.QtResources("assets", env,
            files=["images/*.png", "data/config.json"], prefix="/")
        app.link(res)

    Files are embedded under ``<prefix>/<path relative to base_dir>``,
    e.g. images/logo.png -> :/images/logo.png.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        files: Sequence[str | Path],
        prefix: str = "/",
        base_dir: str | Path | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a resource object target.

        Args:
            project: The project.
            name: Resource name (also the Q_INIT_RESOURCE name).
            env: Environment; must have the qt toolchain.
            files: Files to embed; globs are expanded (sorted) relative
                to the project root.
            prefix: Resource prefix (the ``:/...`` root).
            base_dir: Directory aliases are computed relative to
                (default: the project root).
        """
        _require_qt_tool(env, "QtResources()")
        defined_at = defined_at or get_caller_location()
        root = project.root_dir
        base = root / base_dir if base_dir is not None else root

        expanded: list[Path] = []
        for pattern in files:
            pattern_str = str(pattern)
            if any(ch in pattern_str for ch in "*?["):
                matches = sorted(root.glob(pattern_str))
                if not matches:
                    raise FileNotFoundError(
                        f"QtResources '{name}': pattern '{pattern_str}' "
                        f"matched no files under {root}"
                    )
                expanded.extend(matches)
            else:
                expanded.append(root / pattern_str)

        entries: list[tuple[str, Path]] = []
        for path in expanded:
            try:
                alias = path.relative_to(base).as_posix()
            except ValueError:
                alias = path.name
            entries.append((alias, path))

        top_root, res_dir = _qt_gen_dir_for(project, env, "qt.res")
        qrc_rel = res_dir / f"{name}.qrc"
        _write_if_changed(top_root / qrc_rel, _qrc_xml(prefix, entries))

        cpp_node = env.qt.Rcc(res_dir / f"qrc_{name}.cpp", qrc_rel, name=name)[0]
        # getattr: the generated builder stubs omit the internal
        # defined_at parameter, but passing it keeps "defined at"
        # diagnostics pointing at the user's call site.
        factory = getattr(project, "ObjectLibrary")  # noqa: B009
        target: Target = factory(name, env, sources=[cpp_node], defined_at=defined_at)
        return target
