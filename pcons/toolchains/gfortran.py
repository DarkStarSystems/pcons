# SPDX-License-Identifier: MIT
"""GNU Fortran (gfortran) toolchain implementation.

Provides gfortran-based Fortran compilation toolchain including:
- GNU Fortran compiler (gfortran)
- GNU archiver (ar) - reused from gcc
- Linker (using gfortran for proper runtime linkage)
- Ninja dyndep support for correct Fortran module dependency ordering
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler  # noqa: F401

logger = logging.getLogger(__name__)

# Fortran source file extensions
_FORTRAN_FREE_FORM = {".f90", ".f95", ".f03", ".f08", ".f18"}
_FORTRAN_PREPROCESSED = {".F", ".F90"}
_FORTRAN_FIXED_FORM = {".f", ".for", ".ftn"}
FORTRAN_EXTENSIONS = _FORTRAN_FREE_FORM | _FORTRAN_PREPROCESSED | _FORTRAN_FIXED_FORM


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
    """GNU Fortran compiler tool.

    Variables:
        cmd: Compiler command (default: 'gfortran')
        flags: General compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        depflags: Dependency generation flags
        moddir: Module output/search directory (default: 'modules')
        objcmd: Command template for compiling to object
    """

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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        gfortran = config.find_program("gfortran")
        if gfortran is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("fc", cmd=str(gfortran.path))
        if gfortran.version:
            tool_config.version = gfortran.version
        return tool_config


class GfortranLinker(BaseTool):
    """GNU Fortran linker tool.

    Uses gfortran as the linker command to ensure proper Fortran runtime linkage.

    Variables: same as GccLinker but cmd defaults to 'gfortran'
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        shared_flag = "-dynamiclib" if platform.is_macos else "-shared"
        return {
            "cmd": "gfortran",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "Fprefix": "-F",
            "frameworkdirs": [],
            "fprefix": "-framework",
            "frameworks": [],
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
                "${prefix(link.Fprefix, link.frameworkdirs)}",
                "${pairwise(link.fprefix, link.frameworks)}",
            ],
            "sharedcmd": [
                "$link.cmd",
                shared_flag,
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
                "${prefix(link.Fprefix, link.frameworkdirs)}",
                "${pairwise(link.fprefix, link.frameworks)}",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.exe_suffix],
                single_source=False,
            ),
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                outputs=[
                    OutputSpec("primary", platform.shared_lib_suffix),
                ],
                src_suffixes=[platform.object_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        gfortran = config.find_program("gfortran")
        if gfortran is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(gfortran.path))


class GfortranToolchain(UnixToolchain):
    """GNU Fortran toolchain.

    Includes: Fortran compiler (gfortran), archiver (ar), linker (gfortran)

    Supports Ninja dyndep for correct Fortran module dependency ordering.
    """

    # Include "fortran" with priority 3 so Fortran wins over C/C++ when
    # GfortranToolchain is the primary toolchain.
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

    def get_source_handler(self, suffix: str) -> SourceHandler | None:  # type: ignore[override]
        """Return handler for Fortran source file suffixes."""
        from pcons.tools.toolchain import SourceHandler

        # First check Fortran extensions (case-sensitive for .F, .F90)
        if suffix in FORTRAN_EXTENSIONS:
            # No depfile: Fortran has no header includes; module deps use dyndep
            return SourceHandler("fc", "fortran", ".o", None, None)

        # Fall back to C/C++ handlers from UnixToolchain
        return super().get_source_handler(suffix)

    def _linker_for_language(self, language: str) -> str:
        """For Fortran, use the 'link' tool (which is gfortran)."""
        if language == "fortran":
            return "link"
        return super()._linker_for_language(language)

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
        """Set up Ninja dyndep for Fortran module dependencies.

        Called after all targets are resolved. Creates:
        1. A manifest JSON file (at configure time) mapping sources to objects
        2. A Ninja dyndep build step that scans sources for MODULE/USE statements
        3. Attaches dyndep to each Fortran object node

        Args:
            project: The project being built.
            source_obj_by_language: All (source_path, obj_node) pairs grouped
                by language. GfortranToolchain extracts "fortran" entries.
        """
        fortran_source_obj_pairs = source_obj_by_language.get("fortran", [])
        if not fortran_source_obj_pairs:
            return

        build_dir = project.build_dir
        manifest_path = build_dir / "fortran.manifest.json"
        dyndep_path = build_dir / "fortran_modules.dyndep"
        moddir = "modules"  # relative to build dir (ninja runs from build dir)

        # Write manifest at configure time (read by scanner at build time)
        manifest = [
            {
                "src": str(src.resolve()),
                "obj": str(obj.path.relative_to(build_dir)),
            }
            for src, obj in fortran_source_obj_pairs
        ]
        build_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.debug("Wrote Fortran module manifest to %s", manifest_path)

        # Get environment from first Fortran object node for registration
        first_env = None
        _, first_obj = fortran_source_obj_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        # Create source file nodes for scanner dependencies
        source_nodes = [project.node(src) for src, _ in fortran_source_obj_pairs]

        # Create the dyndep scanner build node
        dyndep_node = project.node(dyndep_path)
        dyndep_node.depends(source_nodes)

        # Build the scanner command using sys.executable for portability
        dyndep_node._build_info = {
            "tool": "fc_scanner",
            "command_var": "scancmd",
            "description": "SCAN Fortran modules",
            "sources": source_nodes,
            "command": [
                sys.executable,
                "-m",
                "pcons.toolchains.fortran_scanner",
                "--manifest",
                "fortran.manifest.json",
                "--out",
                "fortran_modules.dyndep",
                "--mod-dir",
                moddir,
            ],
        }

        # Register dyndep node with environment so generator writes its build statement
        if first_env is not None:
            first_env.register_node(dyndep_node)

        # Attach dyndep to each Fortran object node
        dyndep_rel = "fortran_modules.dyndep"
        for _, obj_node in fortran_source_obj_pairs:
            obj_build_info = getattr(obj_node, "_build_info", None)
            if obj_build_info is not None:
                obj_build_info["dyndep"] = dyndep_rel
            obj_node.implicit_deps.append(dyndep_node)


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
    """Find the first available Fortran toolchain.

    Currently only gfortran is supported.

    Args:
        prefer: List of toolchain names to try, in order.
                Defaults to ["gfortran"].

    Returns:
        A configured Fortran toolchain ready for use.

    Raises:
        RuntimeError: If no Fortran toolchain is available.

    Example:
        from pcons.toolchains import find_fortran_toolchain

        toolchain = find_fortran_toolchain()
        env = project.Environment(toolchain=toolchain)
    """
    if prefer is None:
        prefer = ["gfortran"]

    toolchain = toolchain_registry.find_available("fortran", prefer)
    if toolchain is not None:
        return toolchain  # type: ignore[return-value]

    tried = toolchain_registry.get_tried_names("fortran", prefer)
    raise RuntimeError(
        f"No Fortran toolchain found. Tried: {', '.join(tried)}. "
        "Make sure gfortran is installed and in PATH."
    )
