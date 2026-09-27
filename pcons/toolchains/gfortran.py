# SPDX-License-Identifier: MIT
"""GNU Fortran (gfortran) toolchain, with Ninja dyndep for Fortran
module dependency ordering."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.gnu_common import gnu_link_builders, gnu_link_vars
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler  # noqa: F401

# Fortran source file extensions
_FORTRAN_FREE_FORM = {".f90", ".f95", ".f03", ".f08", ".f18"}
_FORTRAN_PREPROCESSED = {".F", ".F90"}
_FORTRAN_FIXED_FORM = {".f", ".for", ".ftn"}
FORTRAN_EXTENSIONS = _FORTRAN_FREE_FORM | _FORTRAN_PREPROCESSED | _FORTRAN_FIXED_FORM

# Default module output/search directory, relative to the build directory.
DEFAULT_MODDIR = "modules"


def _moddir_of(env: object) -> str:
    """The module directory an environment compiles Fortran into."""
    fc = getattr(env, "fc", None)
    return str(getattr(fc, "moddir", None) or DEFAULT_MODDIR)


def _fortran_targets(
    project: Project,
    source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
) -> list[Target]:
    """Every target owning a Fortran object, in declaration order.

    An object a Fortran compile writes may be a target's intermediate (a
    program's or library's own compiles) or its output (a bare ``Object``
    target). Either way it belongs to exactly one target — the dyndep
    contract: one edge, one governing dyndep file.
    """
    fortran_objs = {id(obj) for _, obj in source_obj_by_language.get("fortran", [])}
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


def _find_gfortran_libdir() -> str | None:
    """Return the directory containing libgfortran, or None if not found.

    Used to inject -L<dir> when a C/C++ linker needs to find libgfortran
    (e.g., on macOS where Homebrew installs gfortran's libs in a
    non-standard location).
    """
    import shutil
    import subprocess

    if not shutil.which("gfortran"):
        return None
    # Use libgfortran.a (present on all platforms) to find the lib directory.
    # gfortran returns the full path if found, or just the filename if not.
    try:
        libfile = subprocess.check_output(
            ["gfortran", "--print-file-name=libgfortran.a"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if libfile and libfile != "libgfortran.a":
            return str(Path(libfile).resolve().parent)
    except (subprocess.CalledProcessError, OSError):
        pass
    return None


class GfortranCompiler(BaseTool):
    """GNU Fortran compiler tool. ``moddir`` (default 'modules') is the
    module output/search directory, passed as -J and -I."""

    env_var = "FC"

    def __init__(self) -> None:
        super().__init__("fc", language="fortran")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "gfortran",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "moddir": "modules",
            "objcmd": [
                "$fc.cmd",
                "$fc.flags",
                "${prefix(fc.iprefix, fc.includes)}",
                "${prefix(fc.dprefix, fc.defines)}",
                "-J",
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
        platform = get_platform()
        src_suffixes = sorted(FORTRAN_EXTENSIONS)
        return {
            "Object": CommandBuilder(
                "Object",
                "fc",
                "objcmd",
                src_suffixes=src_suffixes,
                target_suffixes=[platform.object_suffix],
                language="fortran",
                single_source=True,
                # No depfile for Fortran: module deps handled by dyndep,
                # and Fortran doesn't have header includes to track.
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "gfortran", with_version=True)


class GfortranLinker(BaseTool):
    """Linker using gfortran as the driver, for Fortran runtime linkage."""

    env_var = "FC"

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return gnu_link_vars("gfortran")

    def builders(self) -> dict[str, Builder]:
        return gnu_link_builders()

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "gfortran")


class GfortranToolchain(UnixToolchain):
    """GNU Fortran toolchain: gfortran, ar, gfortran as linker.

    Uses Ninja dyndep for Fortran module dependency ordering.
    """

    TOOL_NAMES = ("fc", "ar", "link")

    # Realized on `fc`; -Wpedantic omitted as it is noisy for legal Fortran.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {"compile_flags": ["-Wall", "-Wextra"]},
        "werror": {"compile_flags": ["-Werror"]},
        # gfortran is always real GCC (no Apple-clang shim concern), so
        # these realize statically.
        "openmp": {"compile_flags": ["-fopenmp"], "link_flags": ["-fopenmp"]},
        "coverage": {"compile_flags": ["--coverage"], "link_flags": ["--coverage"]},
        "fast-math": {"compile_flags": ["-ffast-math"], "link_flags": ["-ffast-math"]},
    }

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("fc",)

    # Priority 3 so Fortran wins over C/C++ when this is the primary toolchain.
    @property
    def language_priority(self) -> dict[str, int]:
        return {**self.DEFAULT_LANGUAGE_PRIORITY, "fortran": 3}

    def __init__(self) -> None:
        super().__init__("gfortran")
        self._gfortran_libdir: str | None = _find_gfortran_libdir()

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        fc = GfortranCompiler()
        if fc.configure(config) is None:
            return False

        ar = GccArchiver()
        ar.configure(config)

        link = GfortranLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"fc": fc, "ar": ar, "link": link}
        return True

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for Fortran source file suffixes."""
        from pcons.tools.toolchain import SourceHandler

        # Case-sensitive check (.F/.F90 are preprocessed forms)
        if suffix in FORTRAN_EXTENSIONS:
            # No depfile: Fortran has no header includes; module deps use dyndep
            return SourceHandler("fc", "fortran", ".o", None, None)

        return super().get_source_handler(suffix)

    def get_runtime_libs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Inject C++ or Fortran runtime for mixed C++/Fortran builds.

        - Fortran linker + C++ objects → inject C++ runtime (-lc++ or -lstdc++)
        - C/C++ linker + Fortran objects → inject Fortran runtime (-lgfortran)
        """
        platform = get_platform()
        if linker_language == "fortran" and "cxx" in object_languages:
            return ["c++"] if platform.is_macos else ["stdc++"]
        if linker_language in ("c", "cxx") and "fortran" in object_languages:
            return ["gfortran"]
        return []

    def get_runtime_libdirs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return the gfortran library directory when needed.

        On macOS with Homebrew gfortran, libgfortran is in a non-standard
        location. When C/C++ is the linker and Fortran objects are present,
        inject the path so the linker can find libgfortran.
        """
        if linker_language in ("c", "cxx") and "fortran" in object_languages:
            if self._gfortran_libdir:
                return [self._gfortran_libdir]
        return []

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
        compile that writes it (``-J <moddir>``), and dyndep outputs are
        global to the build, so a module used across targets still resolves
        as long as the using target depends on the providing one.
        """
        from pcons.core.scan import Scanner
        from pcons.core.subst import NodeVar

        targets = _fortran_targets(project, source_obj_by_language)
        if not targets:
            return

        # The compile writes modules with `-J $fc.moddir`; gfortran does not
        # create that directory, and a consuming-only compile never has it as
        # an output for ninja to create. Make it at configure time.
        build_dir_fs = project.top_path_resolver.execution_dir
        for target in targets:
            (build_dir_fs / _moddir_of(target._env)).mkdir(parents=True, exist_ok=True)

        # gfortran leaves a .mod file untouched when recompiling produces an
        # identical one — the very thing that keeps a cosmetic edit from
        # cascading through every dependent. Without restat, ninja then sees
        # that dyndep implicit output as forever older than its source and
        # recompiles on every build; with it, the recorded mtime settles and
        # dependents rebuild only when the module interface really changed.
        for _src, obj in source_obj_by_language.get("fortran", []):
            if obj._build_info is not None:
                obj._build_info["restat"] = True

        def scan_vars(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            # Per-edge, so every scan shares one ninja rule even when two
            # environments name different module directories.
            return {"FC_MODDIR": _moddir_of(env)}

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
                "--out",
                TargetPath(),
            ],
            info_suffix=".fscan.json",
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


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    GfortranToolchain,
    aliases=["gfortran"],
    check_command="gfortran",
    tool_classes=[GfortranCompiler, GccArchiver, GfortranLinker],
    category="fortran",
    platforms=["linux", "darwin"],
    description="GNU Fortran compiler (gfortran)",
    finder="find_fortran_toolchain()",
)


def find_fortran_toolchain(
    prefer: list[str] | None = None,
) -> GfortranToolchain:
    """Find the first available Fortran toolchain (currently only gfortran).

    Args:
        prefer: Toolchain names to try, in order. Defaults to ["gfortran"].

    Returns:
        A configured Fortran toolchain ready for use.

    Raises:
        RuntimeError: If no Fortran toolchain is available.
    """
    if prefer is None:
        prefer = ["gfortran"]

    toolchain = toolchain_registry.find_available("fortran", prefer)
    if toolchain is not None:
        return cast(GfortranToolchain, toolchain)

    tried = toolchain_registry.get_tried_names("fortran", prefer)
    raise RuntimeError(
        f"No Fortran toolchain found. Tried: {', '.join(tried)}. "
        "Make sure gfortran is installed and in PATH."
    )


toolchain_registry.register_finder(
    ["fortran"],
    find_fortran_toolchain,
    description="Auto-detect a Fortran toolchain",
)
