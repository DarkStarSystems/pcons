# SPDX-License-Identifier: MIT
"""LLVM/Clang toolchain implementation."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.environment import Environment
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import CXX_MODULE_INTERFACE_SUFFIXES

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)


def _find_libcxx_modules_manifest(
    compiler_cmd: str, base_flags: list[str]
) -> Path | None:
    """Locate `libc++.modules.json` via `clang -print-file-name`.

    libc++ ships a JSON manifest that points at `std.cppm` /
    `std.compat.cppm` and the system include directories required to
    compile them. We let the compiler tell us where it is — works for any
    libc++ install (Homebrew, apt, vendored).

    Returns the manifest path if found, or None if the toolchain doesn't
    ship one (Apple Clang ≤ 21 is the most common case; users need
    Homebrew LLVM there).
    """
    cmd = [compiler_cmd]
    user_stdlib_flags = [f for f in base_flags if f.startswith("-stdlib=")]
    if user_stdlib_flags:
        cmd.extend(user_stdlib_flags)
    else:
        cmd.append("-stdlib=libc++")
    candidates = (
        "libc++.modules.json",
        "c++/libc++.modules.json",
    )
    for candidate in candidates:
        cmd_copy = list(cmd)
        cmd_copy.append(f"-print-file-name={candidate}")
        try:
            proc = subprocess.run(cmd_copy, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if proc.returncode != 0:
            continue
        out = proc.stdout.strip()
        # When the file isn't found, clang echoes the query string back unchanged.
        if not out or out == candidate:
            continue
        p = Path(out)
        if p.is_file():
            return p.resolve()
    return None


def _parse_libcxx_manifest(manifest: Path) -> dict[str, dict[str, Any]]:
    """Parse `libc++.modules.json` into `{logical_name: {source-path, sys-includes}}`.

    Resolves `source-path` and `system-include-directories` to absolute
    paths (the manifest stores them relative to its own directory).

    Refuses unknown manifest versions: the libc++ team has reserved
    ``version`` for breaking format changes (``revision`` is for
    additive ones we can ignore). If the version doesn't match what
    pcons knows, we raise — silently misparsing a future format would
    produce hard-to-diagnose downstream failures.
    """
    data = json.loads(manifest.read_text(encoding="utf-8"))
    version = data.get("version")
    if version is not None and version != 1:
        raise RuntimeError(
            f"libc++ modules manifest at {manifest} declares version "
            f"{version!r}, but pcons only knows version 1. The format may "
            "have changed in your libc++; please update pcons or file an "
            "issue with the manifest contents."
        )
    base = manifest.parent
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("modules", []) or []:
        ln = entry.get("logical-name")
        sp = entry.get("source-path")
        if not ln or not sp:
            continue
        local = entry.get("local-arguments") or {}
        sys_inc = local.get("system-include-directories") or []
        out[str(ln)] = {
            "source-path": (base / sp).resolve(),
            "system-include-directories": [(base / d).resolve() for d in sys_inc],
        }
    return out


# ABI-affecting flags that must match between the std-module compile and
# the user's TUs that import it. Mismatches here range from silent ABI
# corruption (e.g. -frtti vs -fno-rtti) to link errors (e.g. exception
# model). Adapted from CMake's `cmake-cxxmodules` propagation list +
# libc++ documentation; expand if a user reports a mismatch we missed.
def _clang_std_module_flag_spec() -> Any:
    """Build the clang/libc++ flag-passthrough spec lazily.

    Defined as a function so the import order doesn't force the scanner
    module to be loaded at llvm.py import time (it's an implementation
    detail).
    """
    from pcons.toolchains.cxx_module_scanner import StdModuleFlagSpec

    return StdModuleFlagSpec(
        # Exception/RTTI model, libc++ experimental switch, common ABI knobs.
        exact=frozenset(
            {
                "-fexceptions",
                "-fno-exceptions",
                "-frtti",
                "-fno-rtti",
                "-fexperimental-library",
                "-fno-experimental-library",
                "-pthread",
                "-fopenmp",
                "-stdlib=libc++",
                "-stdlib=libstdc++",
                "-m32",
                "-m64",
            }
        ),
        # `-std=c++23`, `-stdlib=libc++`, `-isysroot=/p`, `-arch=x86_64`,
        # `-march=...`, `--target=...`, plus a handful of ABI-relevant
        # flags that take a value attached to the prefix.
        prefixes=(
            "-std=",
            "-stdlib=",
            "--target=",
            "-isysroot=",
            "--sysroot=",
            "-march=",
            "-mcpu=",
            "-mtune=",
            "-arch=",
        ),
        # GCC-style two-token spellings — Apple Clang in particular uses
        # `-isysroot /path` and `-arch x86_64` rather than the
        # equals-attached form.
        paired=frozenset({"-target", "-isysroot", "-arch", "--sysroot"}),
        # Pass user `-D_LIBCPP_*` defines: the std module is sensitive to
        # libc++ feature-test / configuration macros (e.g.
        # `_LIBCPP_HARDENING_MODE`, `_LIBCPP_ENABLE_EXPERIMENTAL`).
        # `__GLIBCXX__` is included for forward-compat in case libstdc++
        # ever ships its own modules manifest.
        define_prefix="-D",
        define_glob_prefixes=("_LIBCPP_", "__GLIBCXX_"),
    )


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
            "modules": False,  # set True to enable C++20 module scanning
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

        # First check base Unix toolchain for standard C/C++/Objective-C/assembly.
        # Then replace the hardcoded ".o" with the platform object suffix so that
        # on Windows we get ".obj" (Clang on Windows uses MSVC object file conventions).
        handler = super().get_source_handler(suffix)
        if handler is not None:
            obj_suffix = get_platform().object_suffix
            if handler.object_suffix != obj_suffix:
                handler = SourceHandler(
                    handler.tool_name,
                    handler.language,
                    obj_suffix,
                    handler.depfile,
                    handler.deps_style,
                )
            return handler

        # C++20 module interface units
        if suffix in CXX_MODULE_INTERFACE_SUFFIXES:
            depfile = TargetPath(suffix=".d")
            return SourceHandler(
                "cxx", "cxx_module", get_platform().object_suffix, depfile, "gcc"
            )

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
        """Configure C++20 module compilation (LLVM/Clang).

        Runs `clang-scan-deps` at configure time on every C++ TU in any
        target that uses modules, and uses the scan output to drive flag
        injection. Module-providing TUs get `-x c++-module` and
        `-fmodule-output=<pcm>` regardless of file extension; the PCM path
        comes from the logical module name (so partitions like
        `M:P` resolve to `<moddir>/M-P.pcm`).
        """
        from pcons.toolchains.cxx_module_scanner import (
            TuScanSpec,
            build_module_map,
            scan_translation_units,
            select_modules_scope,
            wire_std_into_targets,
            write_dyndep_from_results,
        )

        cxx_module_pairs, cxx_pairs = select_modules_scope(source_obj_by_language)
        all_cxx_pairs = cxx_module_pairs + cxx_pairs
        if not all_cxx_pairs:
            return

        build_dir = project.build_dir
        moddir = "cxx_modules"
        dyndep_path = build_dir / "cxx_modules.dyndep"
        dyndep_rel = "cxx_modules.dyndep"

        first_env = None
        _, first_obj = all_cxx_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        cxx_tool = getattr(first_env, "cxx", None) if first_env else None
        compiler_cmd = str(getattr(cxx_tool, "cmd", "clang++") or "clang++")
        base_flags = list(getattr(cxx_tool, "flags", None) or [])

        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / moddir).mkdir(exist_ok=True)

        # -fprebuilt-module-path on every C++ TU so importers find PCMs.
        modpath_flag = f"-fprebuilt-module-path={moddir}"
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if modpath_flag not in context.flags:
                        context.flags.append(modpath_flag)

        # Pre-flag extension-tagged module units with -x c++-module so the
        # scanner sees them as modules (clang doesn't recognize .ixx natively).
        # The scan output may identify *additional* TUs (e.g., partition units
        # in .cpp files) as module providers — those get flagged below.
        for _, obj_node in cxx_module_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi:
                context = bi.get("context")
                if context is not None and hasattr(context, "flags"):
                    if "-x" not in context.flags:
                        context.flags.extend(["-x", "c++-module"])

        # Build per-TU scan specs.
        specs: list[TuScanSpec] = []
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            context = bi.get("context") if bi else None
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

            specs.append(
                TuScanSpec(
                    src=src.resolve(),
                    obj_rel=str(obj_node.path.relative_to(build_dir)).replace(
                        "\\", "/"
                    ),
                    compiler=compiler_cmd,
                    compile_flags=compile_flags,
                )
            )

        results = scan_translation_units(
            specs, scanner="clang-scan-deps", scanner_style="clang"
        )

        # `import std;` / `import std.compat;` support: if any TU requires
        # the standard library module, locate libc++'s std.cppm via the
        # manifest the compiler ships and synthesize a build node for it.
        # Appended to `results` so build_module_map() registers it and the
        # dyndep file lists the .pcm as an implicit output.
        std_obj_nodes = self._inject_clang_std_module_builds(
            project, build_dir, moddir, compiler_cmd, base_flags, results, first_env
        )

        module_to_pcm = build_module_map(results, moddir, ".pcm")

        # For each module-providing TU (interfaces, partition interfaces,
        # internal partitions), inject -x c++-module and -fmodule-output.
        spec_to_obj = {
            id(spec): obj_node
            for spec, (_, obj_node) in zip(specs, all_cxx_pairs, strict=True)
        }
        for r in results:
            if not r.is_module_provider:
                continue
            # Skip synthetic std-module entries — their flags are already in
            # the literal command list, not in a CompileLinkContext.
            if id(r.spec) not in spec_to_obj:
                continue
            obj_node = spec_to_obj[id(r.spec)]
            bi = getattr(obj_node, "_build_info", None)
            if bi is None:
                continue
            context = bi.get("context")
            if context is None or not hasattr(context, "flags"):
                continue
            pcm_path = module_to_pcm[r.logical_name]
            module_out_flag = f"-fmodule-output={pcm_path}"
            if module_out_flag not in context.flags:
                context.flags.append(module_out_flag)
            if "-x" not in context.flags:
                context.flags.extend(["-x", "c++-module"])

        write_dyndep_from_results(results, module_to_pcm, dyndep_path)
        logger.debug("Wrote C++ module dyndep to %s", dyndep_path)

        dyndep_node = project.node(dyndep_path)
        for _, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi is not None:
                bi["dyndep"] = dyndep_rel
            obj_node.implicit_deps.append(dyndep_node)
        for std_obj_node in std_obj_nodes.values():
            std_bi = std_obj_node._build_info
            assert std_bi is not None  # set in _inject_clang_std_module_builds
            std_bi["dyndep"] = dyndep_rel
            std_obj_node.implicit_deps.append(dyndep_node)

        if std_obj_nodes:
            wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)

    def _inject_clang_std_module_builds(
        self,
        project: Project,
        build_dir: Path,
        moddir: str,
        compiler_cmd: str,
        base_flags: list[str],
        results: list[Any],
        first_env: Environment | None,
    ) -> dict[str, FileNode]:
        """Synthesize build nodes for `import std;` / `import std.compat;` (clang).

        If the scan reports that any TU requires the `std` or `std.compat`
        logical module, locate libc++'s `libc++.modules.json` (via
        `-print-file-name`), find the corresponding `.cppm` source and the
        system include dirs, and create a build node that compiles them
        with the user's `-std=` / `-stdlib=` flags. A synthetic
        TuScanResult is appended to `results` so the dyndep file declares
        the resulting `.pcm` as an implicit output.

        Returns:
            Dict mapping logical module name -> std obj FileNode for the
            modules that were synthesized.
        """
        from pcons.toolchains.cxx_module_scanner import TuScanResult, TuScanSpec

        required_logical_names: set[str] = set()
        for r in results:
            for ln in r.required_logical_names:
                required_logical_names.add(ln)

        wanted = required_logical_names & {"std", "std.compat"}
        if not wanted:
            return {}

        manifest = _find_libcxx_modules_manifest(compiler_cmd, base_flags)
        if manifest is None:
            raise RuntimeError(
                "`import std;` was used, but pcons could not locate libc++'s "
                "C++ standard-library module manifest. Tried\n"
                f"    {compiler_cmd} -stdlib=libc++ "
                "-print-file-name=c++/libc++.modules.json\n"
                "and got no usable path. On macOS, install Homebrew LLVM "
                "(`brew install llvm`) — Apple Clang doesn't ship the std "
                "module yet. On Linux, install a recent libc++ that includes "
                "`libc++.modules.json` (LLVM ≥ 18). Alternatively use a "
                "different toolchain (MSVC works on Windows, GCC ≥ 15 works on Linux)."
            )
        modules = _parse_libcxx_manifest(manifest)

        # Pick ABI-affecting flags from the user's compile flags AND from
        # env.cxx.defines (where users typically put `_LIBCPP_HARDENING_MODE`
        # and other libc++ feature-test macros).
        from pcons.toolchains.cxx_module_scanner import select_std_module_flags

        cxx_tool = getattr(first_env, "cxx", None) if first_env else None
        env_defines = list(getattr(cxx_tool, "defines", None) or [])
        dprefix = str(getattr(cxx_tool, "dprefix", "-D") or "-D")
        all_user_flags = list(base_flags) + [f"{dprefix}{d}" for d in env_defines]

        passthrough = select_std_module_flags(
            all_user_flags, _clang_std_module_flag_spec()
        )
        # The std module needs at least C++20 and libc++; if the user
        # didn't say, default sensibly so the std-module compile doesn't
        # fail in a confusing way.
        if not any(f.startswith("-std=") for f in passthrough):
            passthrough.insert(0, "-std=c++20")
        if not any(f.startswith("-stdlib=") for f in passthrough):
            passthrough.append("-stdlib=libc++")

        std_obj_nodes: dict[str, FileNode] = {}
        for logical in sorted(wanted):
            if logical not in modules:
                logger.warning(
                    "import %s requested but not in libc++ manifest %s; skipping",
                    logical,
                    manifest,
                )
                continue
            entry = modules[logical]
            cppm_path: Path = entry["source-path"]
            sys_includes: list[Path] = entry["system-include-directories"]
            if not cppm_path.is_file():
                logger.warning(
                    "import %s: manifest pointed at %s which doesn't exist; skipping",
                    logical,
                    cppm_path,
                )
                continue

            pcm_rel = f"{moddir}/{logical}.pcm"
            obj_rel = f"{moddir}/{logical}.o"
            obj_path = build_dir / obj_rel

            std_obj_node = project.node(obj_path)
            cmd_list: list[str] = [
                compiler_cmd,
                *passthrough,
                # `std` starts with a reserved identifier and libc++'s
                # std.cppm uses reserved user-defined literals; both
                # warn under -Werror unless suppressed.
                "-Wno-reserved-module-identifier",
                "-Wno-reserved-identifier",
                "-Wno-reserved-user-defined-literal",
                *(f"-isystem{d}" for d in sys_includes),
                "-x",
                "c++-module",
                f"-fmodule-output={pcm_rel}",
                "-c",
                str(cppm_path),
                "-o",
                obj_rel,
            ]
            std_obj_node._build_info = {
                "tool": "cxx",
                "command_var": "stdmodcmd",
                "description": f"CXX {logical} module",
                "sources": [project.node(cppm_path)],
                "command": cmd_list,
            }
            if first_env is not None:
                first_env.register_node(std_obj_node)

            synthetic_spec = TuScanSpec(
                src=cppm_path,
                obj_rel=obj_rel,
                compiler=compiler_cmd,
                compile_flags=[],
            )
            synthetic_p1689 = {
                "rules": [
                    {
                        "primary-output": obj_rel,
                        "provides": [{"logical-name": logical, "is-interface": True}],
                    }
                ]
            }
            results.append(TuScanResult(spec=synthetic_spec, p1689=synthetic_p1689))
            std_obj_nodes[logical] = std_obj_node

        return std_obj_nodes


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
