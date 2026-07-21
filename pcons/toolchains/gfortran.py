# SPDX-License-Identifier: MIT
"""GNU Fortran (gfortran) toolchain, with Ninja dyndep for Fortran
module dependency ordering."""

from __future__ import annotations

import json
import logging
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
    """GNU Fortran compiler tool. ``moddir`` (default 'modules') is the
    module output/search directory, passed as -J and -I."""

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
        """Set up Ninja dyndep for Fortran module dependencies.

        Writes a source-to-object manifest at configure time, adds a
        build-time dyndep scanner step, and attaches the dyndep file to
        each Fortran object node.
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

        first_env = None
        _, first_obj = fortran_source_obj_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        source_nodes = [project.node(src) for src, _ in fortran_source_obj_pairs]

        dyndep_node = project.node(dyndep_path)
        dyndep_node.depends(source_nodes)

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

        # Register so the generator writes the dyndep build statement
        if first_env is not None:
            first_env.register_node(dyndep_node)

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
