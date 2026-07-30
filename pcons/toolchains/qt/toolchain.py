# SPDX-License-Identifier: MIT
"""The ``qt`` toolchain: Qt's code-generation tools (moc, uic, rcc).

Added alongside a C/C++ toolchain, normally by :func:`~pcons.toolchains.qt.find_qt`:

    qt = find_qt(project, env, modules=["Widgets"])   # env.add_toolchain("qt") inside

or standalone when Qt's tools are discoverable without module lookup:

    env.add_toolchain("qt")

The tool namespace is ``env.qt``: tool paths (``env.qt.moc``), flag lists
(``env.qt.mocflags``), and the low-level builders:

    moc_cpp = env.qt.Moc(source="mainwindow.h")      # → moc_mainwindow.cpp
    dot_moc = env.qt.Moc(source="widget.cpp")        # → widget.moc (must be
                                                     #   #included by widget.cpp)
    ui_hdr  = env.qt.Uic(source="mainwindow.ui")     # → ui_mainwindow.h
    res_cpp = env.qt.Rcc(source="icons.qrc")         # → qrc_icons.cpp

Every generated edge is incrementally correct via depfiles: moc runs with
``--output-dep-file`` (re-runs when transitively-included headers change)
and rcc with ``--depfile`` (re-runs when files listed in the .qrc change).
There is no build-time scanning and no aggregate ``mocs_compilation.cpp``:
each moc output is its own small, visible ninja edge.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.node import FileNode
from pcons.core.subst import SourcePath, TargetPath
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain, toolchain_registry

if TYPE_CHECKING:
    from pcons.core.builder import Builder, OutputGroup
    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.toolconfig import ToolConfig
    from pcons.toolchains.qt.finder import QtPackage

#: Header suffixes moc treats as "header mode" (output: moc_<stem>.cpp).
MOC_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")
#: Source suffixes moc treats as "source mode" (output: <stem>.moc).
MOC_SOURCE_SUFFIXES = (".cpp", ".cc", ".cxx", ".C")


def _locate_tool_dirs() -> list[Path]:
    """Directories that may hold moc/uic/rcc, best first.

    Qt >= 6.1 keeps build tools in libexec on Unix (bin on Windows).
    pkg-config's Qt6Core.pc records the libexec dir directly; otherwise
    fall back to PATH.
    """
    from pcons.packages.finders.pkgconfig import PkgConfigFinder

    dirs: list[Path] = []
    finder = PkgConfigFinder()
    if finder.is_available():
        for var in ("libexecdir", "bindir"):
            value = finder.get_variable("Qt6Core", var)
            if value:
                dirs.append(Path(value))
    return dirs


def _find_tool(name: str, extra_dirs: list[Path] | None = None) -> Path | None:
    """Find one Qt tool in the given dirs, then on PATH."""
    exe = f"{name}.exe" if get_platform().is_windows else name
    for directory in extra_dirs or []:
        candidate = directory / exe
        if candidate.is_file():
            return candidate
    which = shutil.which(name)
    return Path(which) if which else None


def _qt_gen_dir(env: Environment) -> Path:
    """Default output dir for low-level Qt codegen: <build_dir>/qt.gen."""
    return Path(env.get("build_dir", "build")) / "qt.gen"


def _source_path(source: Node) -> Path:
    """The filesystem path of a source node."""
    if isinstance(source, FileNode):
        return source.path
    return Path(str(source))


def _source_rel_dir(env: Environment, source: Node) -> tuple[str, ...]:
    """Project-relative dir parts of a source, for collision-free layout."""
    project = getattr(env, "_project", None)
    parent = _source_path(source).parent
    if project is not None:
        parent = project._path_resolver.normalize_source_path(parent)
    return tuple(p for p in parent.parts if p not in ("..", "/", "."))


class _QtGenBuilder(CommandBuilder):
    """Shared base: default targets mirror the source's project relpath.

    Layout: ``<build_dir>/qt.gen/<src-rel-dir>/<generated name>``. The
    relpath keeps same-named files in different directories collision-free.
    Subclasses define the generated file's name via _output_name().
    """

    def _output_name(self, source: Node) -> str:
        raise NotImplementedError

    def _default_targets(self, sources: list[Node], env: Environment) -> list[Path]:
        gen_dir = _qt_gen_dir(env)
        result: list[Path] = []
        for src in sources:
            rel = _source_rel_dir(env, src)
            result.append(gen_dir.joinpath(*rel) / self._output_name(src))
        return result

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        if not sources:
            # Catch the classic slip of passing source= (the builder call
            # signature is (target, sources)) — silently building nothing
            # would be far more confusing than this error.
            raise ValueError(
                f"env.qt.{self.name}() needs sources, e.g. "
                f'env.qt.{self.name}(sources="file{self.src_suffixes[0]}").'
            )
        return super()._build(env, targets, sources, **kwargs)


class MocBuilder(_QtGenBuilder):
    """Run moc: header → ``moc_<stem>.cpp``, source → ``<stem>.moc``.

    A generated ``moc_*.cpp`` is compiled as its own translation unit.
    A generated ``*.moc`` is never compiled; the source it came from must
    ``#include`` it (conventionally as the last line).
    """

    def _output_name(self, source: Node) -> str:
        path = _source_path(source)
        if path.suffix in MOC_SOURCE_SUFFIXES:
            return f"{path.stem}.moc"
        return f"moc_{path.stem}.cpp"


class UicBuilder(_QtGenBuilder):
    """Run uic: ``<stem>.ui`` → ``ui_<stem>.h``.

    The output directory must be on the include path of every TU that
    does ``#include "ui_<stem>.h"`` (QtProgram wires this automatically).
    """

    def _output_name(self, source: Node) -> str:
        return f"ui_{_source_path(source).stem}.h"


class RccBuilder(_QtGenBuilder):
    """Run rcc: ``<stem>.qrc`` → ``qrc_<stem>.cpp``.

    The resource name (``rcc --name``, used by Q_INIT_RESOURCE) defaults
    to the .qrc stem; override with ``env.qt.Rcc(..., name="assets")``.
    """

    def _output_name(self, source: Node) -> str:
        return f"qrc_{_source_path(source).stem}.cpp"

    def _build(
        self,
        env: Environment,
        targets: list[Path],
        sources: list[Node],
        **kwargs: Any,
    ) -> list[Node] | OutputGroup:
        name = kwargs.pop("name", None)
        nodes = super()._build(env, targets, sources, **kwargs)
        for node in nodes:
            info = getattr(node, "_build_info", None)
            if info is not None:
                node_sources = info.get("sources") or []
                default = _source_path(node_sources[0]).stem if node_sources else "res"
                info["vars"] = {"RCCNAME": name or default}
        return nodes


class QtTool(BaseTool):
    """Qt code-generation tool suite (namespace ``env.qt``).

    Variables:
        moc, uic, rcc: Tool paths.
        mocflags, uicflags, rccflags: Extra flags for each tool.
        mocdefines, mocincludes: Defines/include dirs moc parses with
            (moc does not inherit the compiler's; QtProgram fills these
            from the consuming target).
        mocpredefs: Compiler-predefined-macro injection, either [] or
            ["--include", <path to moc_predefs.h>].
    """

    def __init__(
        self,
        *,
        moc: str = "moc",
        uic: str = "uic",
        rcc: str = "rcc",
    ) -> None:
        super().__init__("qt")
        self._moc = moc
        self._uic = uic
        self._rcc = rcc

    def default_vars(self) -> dict[str, object]:
        return {
            "moc": self._moc,
            "uic": self._uic,
            "rcc": self._rcc,
            # Build-time helpers run under the same Python running pcons.
            "python": sys.executable,
            "iprefix": "-I",
            "dprefix": "-D",
            "mocflags": [],
            "mocdefines": [],
            "mocincludes": [],
            "mocpredefs": [],
            "uicflags": [],
            "rccflags": [],
            # moc's own depfile options make each edge incrementally
            # correct: it re-runs when any transitively-included header
            # changes, with no build-time scanning machinery.
            "moccmd": [
                "$qt.moc",
                "$qt.mocflags",
                "${prefix(qt.dprefix, qt.mocdefines)}",
                "${prefix(qt.iprefix, qt.mocincludes)}",
                "$qt.mocpredefs",
                "--output-dep-file",
                "--dep-file-path",
                TargetPath(suffix=".d"),
                "-o",
                TargetPath(),
                SourcePath(),
            ],
            "uiccmd": [
                "$qt.uic",
                "$qt.uicflags",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
            # --depfile tracks the files listed in the .qrc, so edits to
            # any embedded resource re-run rcc.
            "rcccmd": [
                "$qt.rcc",
                "$qt.rccflags",
                "--name",
                "$RCCNAME",
                "--depfile",
                TargetPath(suffix=".d"),
                "-o",
                TargetPath(),
                SourcePath(),
            ],
            # Staleness guard for the generate-time moc scan ($in is the
            # scan manifest); its depfile lists every scanned file and
            # directory, so it re-checks exactly when the scan could
            # change — and fails with a "re-run pcons" message if it did.
            "scancheckcmd": [
                "$qt.python",
                "-m",
                "pcons.toolchains.qt._scan_check",
                "--manifest",
                SourcePath(),
                "-o",
                TargetPath(),
                "--depfile",
                TargetPath(suffix=".d"),
            ],
            # Compiler-predefined macros for moc (GCC/Clang only).
            "predefscmd": [
                "$qt.python",
                "-m",
                "pcons.toolchains.qt._moc_predefs",
                "--cxx",
                "$cxx.cmd",
                "-o",
                TargetPath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Moc": MocBuilder(
                "Moc",
                "qt",
                "moccmd",
                src_suffixes=list(MOC_HEADER_SUFFIXES + MOC_SOURCE_SUFFIXES),
                target_suffixes=[".cpp", ".moc"],
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
            "Uic": UicBuilder(
                "Uic",
                "qt",
                "uiccmd",
                src_suffixes=[".ui"],
                target_suffixes=[".h"],
                single_source=True,
            ),
            "Rcc": RccBuilder(
                "Rcc",
                "qt",
                "rcccmd",
                src_suffixes=[".qrc"],
                target_suffixes=[".cpp"],
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
            # Internal builders used by QtProgram (not part of the
            # public per-file API, but harmless to call directly).
            "ScanCheck": CommandBuilder(
                "ScanCheck",
                "qt",
                "scancheckcmd",
                src_suffixes=[".json"],
                target_suffixes=[".ok"],
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
                restat=True,
            ),
            "Predefs": CommandBuilder(
                "Predefs",
                "qt",
                "predefscmd",
                target_suffixes=[".h"],
                restat=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        """Locate moc/uic/rcc (pkg-config's libexecdir, then PATH)."""
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        dirs = _locate_tool_dirs()
        moc = _find_tool("moc", dirs)
        if moc is None:
            return None
        self._moc = str(moc)
        uic = _find_tool("uic", dirs)
        if uic is not None:
            self._uic = str(uic)
        rcc = _find_tool("rcc", dirs)
        if rcc is not None:
            self._rcc = str(rcc)

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("qt", cmd=self._moc)


class QtToolchain(BaseToolchain):
    """Qt toolchain, added alongside a C/C++ toolchain.

    Provides the ``env.qt`` namespace. Normally constructed by
    :func:`~pcons.toolchains.qt.find_qt`, which also wires the discovered
    tool paths; ``env.add_toolchain("qt")`` works when Qt's tools are
    discoverable via pkg-config or PATH.
    """

    TOOL_NAMES = ("qt",)

    def __init__(self) -> None:
        super().__init__("qt")

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        qt = QtTool()
        if qt.configure(config) is None:
            return False
        self._tools = {"qt": qt}
        return True

    @classmethod
    def from_package(cls, qt: QtPackage) -> QtToolchain:
        """A toolchain preconfigured from a located Qt installation."""
        toolchain = cls()
        moc = qt.tool_path("moc", required=True)
        uic = qt.tool_path("uic")
        rcc = qt.tool_path("rcc")
        toolchain._tools = {
            "qt": QtTool(
                moc=str(moc),
                uic=str(uic) if uic else "uic",
                rcc=str(rcc) if rcc else "rcc",
            )
        }
        toolchain._configured = True
        return toolchain


def find_qt_toolchain() -> QtToolchain | None:
    """Return a QtToolchain if Qt's tools are discoverable, else None.

    For module/library discovery use :func:`pcons.toolchains.qt.find_qt`,
    which also returns the Qt modules and version.
    """
    dirs = _locate_tool_dirs()
    moc = _find_tool("moc", dirs)
    if moc is None:
        return None
    toolchain = QtToolchain()
    toolchain._tools = {
        "qt": QtTool(
            moc=str(moc),
            uic=str(_find_tool("uic", dirs) or "uic"),
            rcc=str(_find_tool("rcc", dirs) or "rcc"),
        )
    }
    toolchain._configured = True
    return toolchain


def _qt_available() -> bool:
    """Cheap availability probe: any route to moc counts."""
    return _find_tool("moc", _locate_tool_dirs()) is not None


# =============================================================================
# Registration
# =============================================================================

toolchain_registry.register(
    QtToolchain,
    aliases=["qt", "qt6"],
    check_command="moc",
    tool_classes=[QtTool],
    category="qt",  # Its own category: added alongside a C/C++ toolchain
    platforms=["linux", "darwin", "win32"],
    description="Qt code generation tools (moc, uic, rcc)",
    finder="find_qt_toolchain()",
    is_available=_qt_available,
)

toolchain_registry.register_finder(
    ["qt", "qt6"],
    find_qt_toolchain,
    description="Auto-detect the Qt tool suite (moc/uic/rcc)",
)
