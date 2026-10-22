# SPDX-License-Identifier: MIT
"""What every Fortran toolchain shares: source suffixes, the compile and
link tools' command lines, module dependency ordering (Ninja dyndep via
the Scanner), mixed-language runtime injection, and auto-detection.

Each compiler (gfortran, flang, ifx) subclasses these with its command
names, module-directory flag, presets and runtime libraries.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.errors import PconsRuntimeError, PconsValueError
from pcons.core.subst import ColorFlag, PathToken, SourcePath, TargetPath
from pcons.toolchains.fortran_scanner import (
    DEFAULT_ANCESTOR_FILE,
    DEFAULT_SUBMODULE_FILE,
)
from pcons.toolchains.gnu_common import gnu_link_builders, gnu_link_vars
from pcons.toolchains.unix import UnixToolchain, static_archive
from pcons.tools.tool import BaseTool, resolve_env_cmd_override
from pcons.tools.toolchain import BaseToolchain, LinkNeeds

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.preset import ToolContribution
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.tools.toolchain import SourceHandler

# Fortran source file extensions
_FORTRAN_FREE_FORM = {".f90", ".f95", ".f03", ".f08", ".f18"}
# Run through the preprocessor first (the capital is what says so)
FORTRAN_PREPROCESSED = {".F", ".F90"}
_FORTRAN_FIXED_FORM = {".f", ".for", ".ftn"}
FORTRAN_EXTENSIONS = _FORTRAN_FREE_FORM | FORTRAN_PREPROCESSED | _FORTRAN_FIXED_FORM

# Default module output/search directory, relative to the build directory.
DEFAULT_MODDIR = "modules"

# Tried in order when nothing names a Fortran compiler.
DEFAULT_PREFERENCE = ("gfortran", "flang", "ifx")


def _fc_var(env: object, name: str, default: str) -> str:
    """One of an environment's Fortran compiler settings."""
    return str(getattr(getattr(env, "fc", None), name, None) or default)


def _moddir_of(env: object) -> str:
    """The module directory an environment compiles Fortran into."""
    return _fc_var(env, "moddir", DEFAULT_MODDIR)


def _fortran_targets(
    project: Project, compiles: list[tuple[Path, FileNode]]
) -> list[Target]:
    """Every target owning one of these Fortran objects, in declaration order.

    An object a Fortran compile writes may be a target's intermediate (a
    program's or library's own compiles) or its output (a bare ``Object``
    target). Either way it belongs to exactly one target — the dyndep
    contract: one edge, one governing dyndep file.
    """
    fortran_objs = {id(obj) for _, obj in compiles}
    if not fortran_objs:
        return []

    targets: list[Target] = []
    claimed: set[int] = set()
    for target in project.targets:
        owned = [
            node
            for node in [*target.intermediate_nodes, *target.output_nodes]
            if id(node) in fortran_objs and id(node) not in claimed
        ]
        if owned:
            claimed.update(id(node) for node in owned)
            targets.append(target)
    return targets


class FortranCompiler(BaseTool):
    """A Fortran compiler. ``moddir`` (default 'modules') is the module
    output/search directory, passed with ``MODDIR_FLAG`` and as ``-I``."""

    env_var = "FC"

    # Executable names, tried in order; the first is the default command.
    COMMANDS: ClassVar[tuple[str, ...]]
    # The flag that sets where compiled .mod files are written.
    MODDIR_FLAG: ClassVar[str]
    # The file a submodule compiles to, from {ancestor} and {name}.
    SUBMODULE_FILE: ClassVar[str] = DEFAULT_SUBMODULE_FILE
    # The file a submodule reads from its ancestor module, from {name}.
    ANCESTOR_FILE: ClassVar[str] = DEFAULT_ANCESTOR_FILE
    # What makes the compiler color its diagnostics (see ColorFlag).
    COLOR_FLAGS: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:
        super().__init__("fc", language="fortran")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": self.COMMANDS[0],
            "colorflags": [ColorFlag(flag) for flag in self.COLOR_FLAGS],
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "moddir": DEFAULT_MODDIR,
            "submodule_file": self.SUBMODULE_FILE,
            "ancestor_file": self.ANCESTOR_FILE,
            "objcmd": [
                "$fc.cmd",
                "$fc.colorflags",
                "$fc.flags",
                "${prefix(fc.iprefix, fc.includes)}",
                "${prefix(fc.dprefix, fc.defines)}",
                self.MODDIR_FLAG,
                "$fc.moddir",
                "-I",
                "$fc.moddir",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object",
                "fc",
                "objcmd",
                src_suffixes=sorted(FORTRAN_EXTENSIONS),
                target_suffixes=[get_platform().object_suffix],
                language="fortran",
                single_source=True,
                # No depfile: the module scan reports both the modules and
                # the included files, which dyndep adds as inputs.
            ),
        }


class FortranLinker(BaseTool):
    """A Fortran compiler driving the link, for Fortran runtime linkage."""

    env_var = "FC"

    COMMANDS: ClassVar[tuple[str, ...]]

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return gnu_link_vars(self.COMMANDS[0])

    def builders(self) -> dict[str, Builder]:
        return gnu_link_builders()


class FortranToolchainBase(BaseToolchain):
    """What a Fortran toolchain is, whichever conventions its tools follow:
    the compiler, an archiver and a linker; Fortran sources; module
    dependency ordering via Ninja dyndep; and the runtimes a mixed-language
    link needs.

    Combined with a conventions base: UnixToolchain (FortranToolchain, for
    GNU-style drivers) or MsvcCompatibleToolchain (ifx on Windows).
    """

    # Subclasses set TOOL_NAMES; like UnixToolchain, this base declares
    # none, so generated stubs list only real toolchains.

    # Each compiler declares its own; C/C++ presets don't carry over.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {}

    COMPILER: ClassVar[type[FortranCompiler]]
    ARCHIVER: ClassVar[type[BaseTool]]
    LINKER: ClassVar[type[BaseTool]]
    # Libraries (no -l prefix) a C/C++-driven link needs for Fortran objects.
    RUNTIME_LIBS: ClassVar[tuple[str, ...]]
    # A runtime library file the compiler can locate with -print-file-name
    # (only for the default _find_runtime_libdir).
    RUNTIME_PROBE_FILE: ClassVar[str]

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("fc",)

    def get_object_suffix(self) -> str:
        """The platform's, as the ``Object`` builder uses: ``.obj`` on
        Windows for every Fortran compiler, MinGW's included."""
        return get_platform().object_suffix

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for Fortran source file suffixes, and only those.

        C and C++ sources belong to the C/C++ toolchain added beside this
        one. Claiming them here, as UnixToolchain does, would compile them
        with the GNU conventions (``.o``, gcc depfiles) even when that
        toolchain is clang-cl or MSVC.
        """
        from pcons.tools.toolchain import SourceHandler

        # Case-sensitive check (.F/.F90 are preprocessed forms)
        if suffix in FORTRAN_EXTENSIONS:
            # No depfile: the module scan reports includes and modules alike
            return SourceHandler("fc", "fortran", self.get_object_suffix(), None, None)
        return None

    @cached_property
    def compiler_path(self) -> str | None:
        """The compiler this toolchain runs: $FC, else the first of its
        command names on PATH."""
        override = resolve_env_cmd_override(FortranCompiler.env_var)
        if override is not None:
            return override
        for name in self.COMPILER.COMMANDS:
            found = shutil.which(name)
            if found is not None:
                return found
        return None

    @property
    def objects_name_runtimes(self) -> bool:
        """Whether compiled objects carry their runtime libraries themselves.

        MSVC-ABI compilers (flang and ifx on Windows, like cl and clang-cl)
        embed ``/DEFAULTLIB`` directives, so a link names no runtime
        library, Fortran's or C++'s; it needs only their directories.
        """
        return False

    @cached_property
    def runtime_libdir(self) -> str | None:
        """Where RUNTIME_LIBS live, when a C/C++ driver won't look there."""
        exe = self.compiler_path
        return self._find_runtime_libdir(exe) if exe else None

    def _find_runtime_libdir(self, compiler: str) -> str | None:
        """Ask *compiler* where RUNTIME_PROBE_FILE is (e.g. Homebrew's
        libgfortran, on macOS)."""
        return self._library_dir(compiler, self.RUNTIME_PROBE_FILE)

    @staticmethod
    def _library_dir(compiler: str, name: str) -> str | None:
        """The directory *compiler* links library file *name* from, if it
        knows (``-print-file-name``)."""
        try:
            libfile = subprocess.check_output(
                [compiler, f"-print-file-name={name}"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, OSError):
            return None
        # The full path if found, or just the name if not.
        if libfile and libfile != name:
            return str(Path(libfile).resolve().parent)
        return None

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """Fortran objects linked by another language's driver need the
        Fortran runtime, and its directory where that driver won't look.
        MSVC-ABI objects name their runtime themselves, so any link needs
        only the directory. What the driver itself knows comes first: a
        GNU-style driver knows the C++ runtime, for an imported C++ library
        with no C++ toolchain beside it."""
        needs = super().link_needs(target, languages, link_language, env)
        if "fortran" not in languages:
            return needs
        libdirs = (self.runtime_libdir,) if self.runtime_libdir else ()
        if self.objects_name_runtimes:
            return needs | LinkNeeds(libdirs=libdirs)
        if link_language == "fortran":
            return needs
        libs = self.RUNTIME_LIBS
        if env.runtime_linkage == "static":
            libs = tuple(static_archive(lib, env.target) for lib in self.RUNTIME_LIBS)
        return needs | LinkNeeds(libs=libs, libdirs=libdirs)

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Order Fortran compiles by module dependency, via the Scanner.

        Configure records only static facts: which targets compile Fortran,
        and each one's module directory. What a source provides (``MODULE``)
        and requires (``USE``) is content, so it flows through a per-compile
        scan edge and a per-target collate at build time — the same wiring
        every scanner gets, so a generated ``.f90`` needs no special case.

        Each provided ``.mod`` becomes a dyndep implicit output of the
        compile that writes it, and dyndep outputs are global to the build,
        so a module used across targets still resolves as long as the using
        target depends on the providing one.
        """
        from pcons.core.scan import Scanner
        from pcons.core.subst import NodeVar

        # This toolchain's own compiles: every toolchain sees the whole
        # project's, and two Fortran toolchains (one per environment) would
        # otherwise each attach a scanner to the other's targets.
        compiles = [
            (src, obj)
            for src, obj in source_obj_by_language.get("fortran", [])
            if (obj._build_info or {}).get("toolchain") is self
        ]
        targets = _fortran_targets(project, compiles)
        if not targets:
            return

        # Compilers don't create the module directory, and a consuming-only
        # compile never has it as an output for ninja to create. Make it at
        # configure time, for each environment that compiles Fortran: a
        # source added with add_sources(env=...) may use its own.
        build_dir_fs = project.top_path_resolver.execution_dir
        moddirs = {
            _moddir_of((obj._build_info or {}).get("env")) for _, obj in compiles
        }
        for moddir in moddirs:
            (build_dir_fs / moddir).mkdir(parents=True, exist_ok=True)

        # gfortran and flang leave a .mod file untouched when recompiling
        # produces an identical one — the very thing that keeps a cosmetic
        # edit from cascading through every dependent. Without restat, ninja
        # then sees that dyndep implicit output as forever older than its
        # source and recompiles on every build; with it, the recorded mtime
        # settles and dependents rebuild only when the module interface
        # really changed.
        for _src, obj in compiles:
            if obj._build_info is not None:
                obj._build_info["restat"] = True

        def scan_vars(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            # Per-edge, so every scan shares one ninja rule even when two
            # environments use different module directories or compilers.
            # The include path is the compile's own: its context holds the
            # environment's directories and those its dependencies add.
            context = (governed._build_info or {}).get("context")
            include_dirs = [
                *getattr(context, "includes", ()),
                *getattr(context, "system_includes", ()),
            ]
            return {
                "FC_SCAN_INCLUDES": [
                    PathToken("-I", str(path), "project") for path in include_dirs
                ],
                "FC_MODDIR": _moddir_of(env),
                "FC_SUBMODULE_FILE": _fc_var(
                    env, "submodule_file", DEFAULT_SUBMODULE_FILE
                ),
                "FC_ANCESTOR_FILE": _fc_var(
                    env, "ancestor_file", DEFAULT_ANCESTOR_FILE
                ),
            }

        fortran_suffixes = tuple(sorted(FORTRAN_EXTENSIONS))
        scanner = Scanner(
            "fortran-modules",
            source_suffixes=fortran_suffixes,
            # Explicit markers, not "$SOURCE"/"$TARGET" strings: a marker
            # parsed out of text can become a slice and flip the command
            # into indexed-output mode, which a scan edge never defines.
            scan_command=[
                sys.executable,
                "-m",
                "pcons.toolchains.fortran_scanner",
                "--scan-one",
                SourcePath(),
                "--moddir",
                NodeVar("FC_MODDIR"),
                "--submodule-file",
                NodeVar("FC_SUBMODULE_FILE"),
                "--ancestor-file",
                NodeVar("FC_ANCESTOR_FILE"),
                NodeVar("FC_SCAN_INCLUDES"),
                "--out",
                TargetPath(),
                "--depfile",
                TargetPath(suffix=".d"),
            ],
            info_suffix=".fscan.json",
            scan_depfile=".d",
            scan_vars=scan_vars,
            # A used module may come from a library outside this build (a
            # system or prebuilt Fortran package), so an unresolved USE is
            # not an error. It is a warning: the other cause is a sibling
            # target that provides it with no depends() or link() between
            # them, which builds in whatever order ninja picks and fails
            # when the provider comes second.
            on_unresolved="warn",
        )
        scanner.attach(*targets)


class FortranToolchain(FortranToolchainBase, UnixToolchain):
    """A GNU-style Fortran driver, an archiver, and the driver as linker."""

    LINK_DRIVER_TOOLS: ClassVar[dict[str, str]] = {"fortran": "fc"}

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """None of these drivers retargets the CPU by flag, on any platform:
        gfortran ignores ``-arch`` (and builds for the host) and flang
        rejects it."""
        raise PconsValueError(
            f"{self.name} cannot retarget the CPU to '{arch}' by flag. Use a "
            f"cross preset (e.g. linux_cross(triple=...)) or a cross "
            f"toolchain instead; see docs/presets.md."
        )


def find_fortran_toolchain(
    prefer: list[str] | None = None,
    abi: str | None = None,
) -> FortranToolchainBase:
    """Find the first available Fortran toolchain.

    Args:
        prefer: Toolchain names to try, in order. Defaults to the compiler
            $FC names, else gfortran, flang, ifx.
        abi: Only a toolchain compatible with this ABI qualifies (see
            BaseToolchain.abi): flang or ifx beside MSVC on Windows, say.

    Returns:
        A configured Fortran toolchain ready for use.

    Raises:
        RuntimeError: If no Fortran toolchain is available.
    """
    from pcons.toolchains import env_selected_toolchain

    if prefer is None:
        # $FC steers auto-detection, like $CC/$CXX for C/C++.
        env_toolchain = env_selected_toolchain("fortran", "FC", abi=abi)
        if env_toolchain is not None:
            return cast(FortranToolchainBase, env_toolchain)
        prefer = list(DEFAULT_PREFERENCE)

    toolchain = toolchain_registry.find_available("fortran", prefer, abi=abi)
    if toolchain is not None:
        return cast(FortranToolchainBase, toolchain)

    tried = toolchain_registry.get_tried_names("fortran", prefer)
    if abi is not None:
        raise toolchain_registry.no_compatible_toolchain(
            "fortran", "Fortran", abi, tried
        )
    raise PconsRuntimeError(
        f"No Fortran toolchain found. Tried: {', '.join(tried)}. "
        "Make sure gfortran, flang or ifx is installed and in PATH."
    )


from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register_finder(
    ["fortran"],
    find_fortran_toolchain,
    description="Auto-detect a Fortran toolchain",
    takes_abi=True,
)
