# SPDX-License-Identifier: MIT
"""High-level Qt target builders: QtProgram, QtSharedLibrary,
QtStaticLibrary, and QtResources.

The Qt wrappers accept ``.ui`` and ``.qrc`` files directly in sources and
run automoc — a generate-time scan of the target's sources and their
project-local headers for Q_OBJECT/Q_GADGET/Q_NAMESPACE:

    qt = find_qt(project, env, modules=["Widgets"])
    app = project.QtProgram("myapp", env,
        sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
        link=[qt.Widgets])

What this automates, per file kind:

- header with Q_OBJECT  -> moc edge -> moc_*.cpp compiled as its own TU
- .cpp with Q_OBJECT    -> moc edge -> *.moc; the .cpp must #include it
  (hard error at generate time if it doesn't)
- .ui                   -> uic edge -> ui_*.h; its dir joins the include
  path of every TU
- .qrc                  -> rcc edge -> qrc_*.cpp compiled and linked

Everything lands under ``build/qt.<target>/``. A scan manifest plus a
cheap build-time check edge make a stale scan (e.g. Q_OBJECT added
without re-running pcons) a loud error instead of a vtable link mystery.

QtResources synthesizes the .qrc from a Python file list (no XML):

    res = project.QtResources("assets", env, files=["images/*.png"], prefix="/")
    app.link(res)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from pcons.core.builder_registry import builder
from pcons.core.node import FileNode, Node
from pcons.core.subst import PathToken
from pcons.toolchains.qt.scan import _HEADER_SUFFIXES, QtScanner
from pcons.toolchains.qt.toolchain import (
    MOC_SOURCE_SUFFIXES,
    _source_path,
    _source_rel_dir,
)
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

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
    build edges (rcc, scan check) don't re-run needlessly.
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
    from pcons.toolchains.qt.finder import _qt_installs

    qt = _qt_installs.get(project)
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


@dataclass
class _QtGenInfo:
    """Internal: what _qt_make_target generated (consumed by QtQmlModule)."""

    qt_env: Environment
    qt_dir: Path  # build-relative, e.g. build/qt.<name>
    moc_header_nodes: list[Node] = field(default_factory=list)
    moc_header_dirs: list[Path] = field(default_factory=list)
    dot_moc_nodes: list[Node] = field(default_factory=list)


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
    build_dir = Path(env.get("build_dir", "build"))
    qt_dir = build_dir / f"qt.{name}"
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
                path.relative_to(project.root_dir / build_dir)
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
    toolchain_name = env.toolchain.name if env.toolchain is not None else ""
    if toolchain_name in ("msvc", "clang-cl"):
        qt_env.qt.mocflags = list(qt_env.qt.mocflags) + ["--compiler-flavor", "msvc"]
    elif env.has_tool("cxx"):
        predefs_node = qt_env.qt.Predefs(qt_dir / "moc_predefs.h")[0]
        qt_env.qt.mocpredefs = [
            "--include",
            PathToken(path=f"qt.{name}/moc_predefs.h", path_type="build"),
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
    moc_nodes: list[Node] = []
    moc_header_dirs: list[Path] = []
    dot_moc_nodes: list[Node] = []
    scan_stamp: Node | None = None
    if automoc and cpp_paths:
        scan_dirs = _scan_include_dirs(project, env, link)
        scanner = QtScanner(project.root_dir, cache_dir=project.root_dir / build_dir)
        scan = scanner.scan_target_sources(
            cpp_paths,
            include_dirs=scan_dirs,
            no_moc=[project.root_dir / p for p in no_moc],
        )
        for source in scan.moc_sources:
            scanner.check_moc_include(source)
        scanner.save_cache()

        if (scan.moc_headers or scan.moc_sources) and not includes:
            logger.warning(
                "Qt target '%s': moc runs with no include paths — pass the "
                "Qt modules via link=[qt.Widgets, ...] at construction so "
                "moc can resolve Qt headers (app.link() afterward is too "
                "late for moc).",
                name,
            )

        for header in scan.moc_headers:
            rel = _source_rel_dir(qt_env, project.node(header))
            target_path = qt_dir.joinpath(*rel) / f"moc_{header.stem}.cpp"
            moc_nodes.append(qt_env.qt.Moc(target_path, str(header))[0])
            moc_header_dirs.append(header.parent)
        for source in scan.moc_sources:
            rel = _source_rel_dir(qt_env, project.node(source))
            target_path = qt_dir.joinpath(*rel) / f"{source.stem}.moc"
            moc_node = qt_env.qt.Moc(target_path, str(source))[0]
            dot_moc_nodes.append(moc_node)
            gen_header_dirs.append(target_path.parent)
            # The .cpp's compile must wait for its .moc.
            project.node(source).depends([moc_node])

        # Manifest + build-time check: a scan whose outcome would change
        # fails the build with "re-run pcons" instead of a link mystery.
        manifest_path = project.root_dir / qt_dir / "scan-manifest.json"
        _write_if_changed(
            manifest_path,
            json.dumps(
                {
                    "version": 1,
                    "target": name,
                    "project_root": str(project.root_dir),
                    "sources": sorted(str(p) for p in cpp_paths),
                    "include_dirs": [str(p) for p in scan_dirs],
                    "no_moc": sorted(str(project.root_dir / p) for p in no_moc),
                    "moc_headers": sorted(str(p) for p in scan.moc_headers),
                    "moc_sources": sorted(str(p) for p in scan.moc_sources),
                },
                indent=1,
                sort_keys=True,
            ),
        )
        scan_stamp = qt_env.qt.ScanCheck(
            qt_dir / "scan.ok", qt_dir / "scan-manifest.json"
        )[0]

    # moc edges wait for predefs and the scan check.
    moc_deps = [n for n in (predefs_node, scan_stamp) if n is not None]
    for moc_node in (*moc_nodes, *dot_moc_nodes):
        moc_node.implicit_deps.extend(moc_deps)

    # ---- the real target -------------------------------------------------
    factory = getattr(project, kind)
    target: Target = factory(
        name,
        qt_env,
        sources=[*plain, *moc_nodes, *qrc_nodes],
        defined_at=defined_at,
    )
    if link:
        target.link(*link)
    for directory in dict.fromkeys(gen_header_dirs):
        target.private.include_dirs.append(directory)
    # Generated headers (ui_*.h, *.moc) and the scan stamp must exist
    # before any of the target's TUs compile.
    for node in (*ui_nodes, *dot_moc_nodes, *moc_deps):
        target.depends(node)

    info = _QtGenInfo(
        qt_env=qt_env,
        qt_dir=qt_dir,
        moc_header_nodes=list(moc_nodes),
        moc_header_dirs=list(dict.fromkeys(moc_header_dirs)),
        dot_moc_nodes=list(dot_moc_nodes),
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
        sources: list[str | Path | Node] | None = None,
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
        sources: list[str | Path | Node] | None = None,
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
        sources: list[str | Path | Node] | None = None,
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

        build_dir = Path(env.get("build_dir", "build"))
        qrc_rel = build_dir / "qt.res" / f"{name}.qrc"
        _write_if_changed(root / qrc_rel, _qrc_xml(prefix, entries))

        qt_env = env.clone()
        cpp_node = qt_env.qt.Rcc(
            build_dir / "qt.res" / f"qrc_{name}.cpp", qrc_rel, name=name
        )[0]
        # getattr: the generated builder stubs omit the internal
        # defined_at parameter, but passing it keeps "defined at"
        # diagnostics pointing at the user's call site.
        factory = getattr(project, "ObjectLibrary")  # noqa: B009
        target: Target = factory(
            name, qt_env, sources=[cpp_node], defined_at=defined_at
        )
        return target
