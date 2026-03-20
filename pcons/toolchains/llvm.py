# SPDX-License-Identifier: MIT
"""LLVM/Clang toolchain implementation."""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pathlib import Path

    from pcons.core.builder import Builder
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)


class ClangCCompiler(BaseTool):
    """Clang C compiler tool."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object",
                "cc",
                "objcmd",
                src_suffixes=[".c"],
                target_suffixes=[platform.object_suffix],
                language="c",
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cc", cmd=str(clang.path))
        if clang.version:
            tool_config.version = clang.version
        return tool_config


class ClangCxxCompiler(BaseTool):
    """Clang C++ compiler tool."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang++",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "moddir": "cxx_modules",
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cxx.cmd",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object",
                "cxx",
                "objcmd",
                src_suffixes=[".cpp", ".cxx", ".cc", ".C"],
                target_suffixes=[platform.object_suffix],
                language="cxx",
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        clangxx = config.find_program("clang++")
        if clangxx is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cxx", cmd=str(clangxx.path))
        if clangxx.version:
            tool_config.version = clangxx.version
        return tool_config


class LlvmArchiver(BaseTool):
    """LLVM archiver tool."""

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        import shutil

        # Prefer llvm-ar if available, otherwise fall back to ar
        ar_cmd = "llvm-ar" if shutil.which("llvm-ar") else "ar"
        return {
            "cmd": ar_cmd,
            "flags": ["rcs"],
            "libcmd": ["$ar.cmd", "$ar.flags", TargetPath(), SourcePath()],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary",
                "ar",
                "libcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.static_lib_suffix],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        ar = config.find_program("llvm-ar")
        if ar is None:
            ar = config.find_program("ar")
        if ar is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ar", cmd=str(ar.path))


class LlvmLinker(BaseTool):
    """LLVM linker tool.

    Variables:
        cmd: Linker command (default: 'clang')
        flags: Linker flags (list)
        lprefix: Library prefix (default: '-l')
        libs: Libraries to link (list of names, no prefix)
        Lprefix: Library directory prefix (default: '-L')
        libdirs: Library directories (list of paths, no prefix)
        Fprefix: Framework directory prefix (default: '-F', macOS only)
        frameworkdirs: Framework directories (list of paths, no prefix)
        fprefix: Framework prefix (default: '-framework', macOS only)
        frameworks: Frameworks to link (list of names, no prefix)
        progcmd: Command template for linking program
        sharedcmd: Command template for linking shared library
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        platform = get_platform()
        shared_flag = "-dynamiclib" if platform.is_macos else "-shared"
        return {
            "cmd": "clang",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            # Framework support (macOS only, but always defined for portability)
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
        clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(clang.path))


class MetalCompiler(BaseTool):
    """Apple Metal shader compiler tool (macOS only).

    Compiles .metal shader files to .air (Apple Intermediate Representation).
    The resulting .air files can be linked with metallib to create .metallib archives.

    Variables:
        cmd: Compiler command (default: 'xcrun metal')
        flags: Compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        metalcmd: Command template for compiling to .air
    """

    def __init__(self) -> None:
        super().__init__("metal", language="metal")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "xcrun",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "metalcmd": [
                "$metal.cmd",
                "metal",
                "$metal.flags",
                "${prefix(metal.iprefix, metal.includes)}",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "MetalObject": CommandBuilder(
                "MetalObject",
                "metal",
                "metalcmd",
                src_suffixes=[".metal"],
                target_suffixes=[".air"],
                language="metal",
                single_source=True,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_macos:
            return None

        # Check if xcrun metal is available
        xcrun = config.find_program("xcrun", version_flag="")
        if xcrun is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("metal", cmd=str(xcrun.path))


class LlvmToolchain(UnixToolchain):
    """LLVM/Clang toolchain for C and C++ development.

    Inherits from UnixToolchain which provides:
    - get_source_handler() for C/C++/Objective-C/assembly files
    - get_object_suffix(), get_static_library_name(), etc.
    - get_compile_flags_for_target_type() for -fPIC handling
    - get_separated_arg_flags() for flags like -arch, -framework, etc.
    - apply_target_arch() for macOS cross-compilation
    - apply_variant() for debug/release/etc. configurations

    Additionally supports Metal shaders on macOS.
    """

    def __init__(self) -> None:
        super().__init__("llvm")

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled.

        Extends the base Unix toolchain handler to add Metal shader support
        on macOS.
        """
        from pcons.tools.toolchain import SourceHandler

        # First check base Unix toolchain for standard C/C++/Objective-C/assembly
        handler = super().get_source_handler(suffix)
        if handler is not None:
            return handler

        # C++20 module interface units
        if suffix == ".cppm":
            depfile = TargetPath(suffix=".d")
            return SourceHandler("cxx", "cxx_module", ".o", depfile, "gcc")

        # Metal shaders (macOS only)
        platform = get_platform()
        if suffix.lower() == ".metal" and platform.is_macos:
            # Metal shaders compile to .air (Apple Intermediate Representation)
            # Uses the 'metal' tool with 'metalcmd' command variable
            return SourceHandler("metal", "metal", ".air", None, None, "metalcmd")

        return None

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cc = ClangCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = ClangCxxCompiler()
        cxx.configure(config)

        ar = LlvmArchiver()
        ar.configure(config)

        link = LlvmLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}

        # Add Metal compiler on macOS (optional - not required for toolchain to work)
        platform = get_platform()
        if platform.is_macos:
            metal = MetalCompiler()
            if metal.configure(config) is not None:
                self._tools["metal"] = metal

        return True

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Set up Ninja dyndep for C++20 module dependencies.

        Called after all targets are resolved. Creates:
        1. A manifest JSON file mapping sources to objects/PCMs
        2. A Ninja dyndep build step that scans sources with clang-scan-deps
        3. Attaches dyndep to each CXX object node (interface + regular)
        """
        cxx_module_pairs = source_obj_by_language.get("cxx_module", [])
        if not cxx_module_pairs:
            return

        cxx_pairs = source_obj_by_language.get("cxx", [])
        all_cxx_pairs = cxx_module_pairs + cxx_pairs

        build_dir = project.build_dir
        moddir = "cxx_modules"
        manifest_path = build_dir / "cxx.manifest.json"
        dyndep_path = build_dir / "cxx_modules.dyndep"

        # Get environment from first cxx_module object node
        first_env = None
        _, first_obj = cxx_module_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        # Get compiler command from env
        cxx_tool = getattr(first_env, "cxx", None) if first_env else None
        compiler_cmd = str(getattr(cxx_tool, "cmd", "clang++") or "clang++")
        base_flags = list(getattr(cxx_tool, "flags", None) or [])

        # Build manifest entries
        manifest = []
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / moddir).mkdir(exist_ok=True)

        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            context = bi.get("context") if bi else None
            is_module_interface = src.suffix == ".cppm"

            # Combine base flags + effective requirement flags
            seen: set[str] = set(base_flags)
            compile_flags = list(base_flags)
            if context:
                for f in context.flags:
                    if f not in seen:
                        compile_flags.append(f)
                        seen.add(f)
                for inc in context.includes:
                    compile_flags.append(f"-I{inc}")
                for d in context.defines:
                    compile_flags.append(f"-D{d}")

            entry: dict[str, object] = {
                "src": str(src.resolve()).replace("\\", "/"),
                "obj": str(obj_node.path.relative_to(build_dir)).replace("\\", "/"),
                "is_module_interface": is_module_interface,
                "compiler": compiler_cmd,
                "compile_flags": compile_flags,
            }
            if is_module_interface:
                entry["pcm"] = f"{moddir}/{src.stem}.pcm"
            manifest.append(entry)

        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.debug("Wrote C++ module manifest to %s", manifest_path)

        # Inject -fprebuilt-module-path into ALL cxx/cxx_module compile contexts
        # (happens before command expansion, so context.flags is mutable)
        modpath_flag = f"-fprebuilt-module-path={moddir}"
        for _src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if modpath_flag not in context.flags:
                        context.flags.append(modpath_flag)

        # Inject -fmodule-output=... for module interface files
        for src, obj_node in cxx_module_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    pcm_path = f"{moddir}/{src.stem}.pcm"
                    module_out_flag = f"-fmodule-output={pcm_path}"
                    if module_out_flag not in context.flags:
                        context.flags.append(module_out_flag)

        # Create source file nodes for scanner dependencies
        all_sources = [src for src, _ in all_cxx_pairs]
        source_nodes = [project.node(src) for src in all_sources]

        # Create the dyndep scanner build node
        dyndep_node = project.node(dyndep_path)
        dyndep_node.depends(source_nodes)
        dyndep_node._build_info = {
            "tool": "cxx_scanner",
            "command_var": "scancmd",
            "description": "SCAN C++ modules",
            "sources": source_nodes,
            "command": [
                sys.executable,
                "-m",
                "pcons.toolchains.cxx_module_scanner",
                "--manifest",
                "cxx.manifest.json",
                "--out",
                "cxx_modules.dyndep",
                "--mod-dir",
                moddir,
            ],
        }

        # Register dyndep node with environment so generator writes its build statement
        if first_env is not None:
            first_env.register_node(dyndep_node)

        # Attach dyndep to all CXX and CXX_MODULE object nodes
        dyndep_rel = "cxx_modules.dyndep"
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi is not None:
                bi["dyndep"] = dyndep_rel
            obj_node.implicit_deps.append(dyndep_node)


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    LlvmToolchain,
    aliases=["llvm", "clang"],
    check_command="clang",
    tool_classes=[
        ClangCCompiler,
        ClangCxxCompiler,
        LlvmArchiver,
        LlvmLinker,
        MetalCompiler,
    ],
    category="c",
    platforms=["linux", "darwin", "win32"],
    description="LLVM/Clang compiler",
    finder="find_c_toolchain()",
)
