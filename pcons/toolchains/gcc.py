# SPDX-License-Identifier: MIT
"""GCC toolchain implementation.

Provides GCC-based C and C++ compilation toolchain including:
- GCC C compiler (gcc)
- GCC C++ compiler (g++)
- GNU archiver (ar)
- Linker (using gcc/g++)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.node import FileNode
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.toolconfig import ToolConfig

logger = logging.getLogger(__name__)


def _gcc_std_module_flag_spec() -> Any:
    """Build the GCC/libstdc++ flag-passthrough spec for std-module compiles.

    ABI-affecting flags that must match between the std-module compile and
    the user TUs that import it. Mirrors the clang spec but without
    -stdlib= (not a GCC flag) and with GCC-specific ABI knobs.
    """
    from pcons.toolchains.cxx_module_scanner import StdModuleFlagSpec

    return StdModuleFlagSpec(
        exact=frozenset(
            {
                # Exceptions / RTTI
                "-fexceptions",
                "-fno-exceptions",
                "-frtti",
                "-fno-rtti",
                # Threading / parallelism
                "-pthread",
                "-fopenmp",
                # Data model / ABI width
                "-m32",
                "-m64",
                # Layout / type ABI
                "-fshort-enums",
                "-fshort-wchar",
                "-fpack-struct",
                "-funsigned-char",
                "-fsigned-char",
                "-funsigned-bitfields",
                "-mms-bitfields",
                # Visibility / symbol ABI
                "-fvisibility-inlines-hidden",
                # Floating point ABI
                "-msoft-float",
                "-mhard-float",
                # Experimental language features
                "-fimplicit-constexpr",
                "-freflection",
                "-fcontracts",
                # Debug / sanitizer ABI modifiers
                "-fno-semantic-interposition",
                "-flto",
            }
        ),
        prefixes=(
            # Language / dialect
            "-std=",
            # Target / sysroot
            "--target=",
            "--sysroot=",
            # CPU / architecture / ABI
            "-march=",
            "-mcpu=",
            "-mtune=",
            "-mabi=",
            "-mfpmath=",
            "-mfloat-abi=",
            # C++ ABI
            "-fabi-version=",
            "-fabi-compat-version=",
            # Visibility
            "-fvisibility=",
            # TLS ABI
            "-ftls-model=",
            # Warnings affecting ABI diagnostics
            "-Wabi=",
            # Sanitizers
            "-fsanitize=",
        ),
        paired=frozenset({"-target", "--sysroot"}),
        # Pass user -D_GLIBCXX_* / -D__GLIBCXX_* defines: libstdc++ uses
        # these for hardening, debug modes, etc. that affect module ABI.
        define_prefix="-D",
        define_glob_prefixes=("_GLIBCXX_", "__GLIBCXX_"),
    )


def _find_gcc_std_module_source(
    compiler_cmd: str,
    logical: str,
    base_flags: list[str],
) -> Path | None:
    """Locate bits/std.cc (or bits/std.compat.cc) from GCC include tracing.

    GCC's p1689 scan output does not carry the standard-library source path.
    Probe the active C++ include root using ``-E -x c++ - -H`` and derive the
    module source from that include root.
    """

    source_name = "std.cc" if logical == "std" else "std.compat.cc"
    filename = f"bits/{source_name}"

    try:
        proc = subprocess.run(
            [
                compiler_cmd,
                *base_flags,
                "-E",
                "-x",
                "c++",
                "-",
                "-H",
            ],
            input=f"#include <{filename}>\n",
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stderr.splitlines()
        line = lines[0] if lines else ""
    except FileNotFoundError:
        return None
    except subprocess.CalledProcessError as e:
        # note: the command may fail, with an error looking like 'module control-line cannot be in included file'
        #       but we still have the resolution at the first line
        lines = e.stderr.splitlines()
        line = lines[0] if lines else ""

    line = line.strip()
    if not line.startswith(". "):
        return None
    return Path(line[2:])


class GccCCompiler(BaseTool):
    """GCC C compiler tool.

    Variables:
        cmd: Compiler command (default: 'gcc')
        flags: General compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "gcc",
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

        gcc = config.find_program("gcc")
        if gcc is None:
            gcc = config.find_program("cc")
        if gcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cc", cmd=str(gcc.path))
        if gcc.version:
            tool_config.version = gcc.version
        return tool_config


class GccCxxCompiler(BaseTool):
    """GCC C++ compiler tool.

    Variables:
        cmd: Compiler command (default: 'g++')
        flags: General compiler flags (list)
        iprefix: Include directory prefix (default: '-I')
        includes: Include directories (list of paths, no prefix)
        dprefix: Define prefix (default: '-D')
        defines: Preprocessor definitions (list of names, no prefix)
        depflags: Dependency generation flags
        objcmd: Command template for compiling to object
    """

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "g++",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
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

        gxx = config.find_program("g++")
        if gxx is None:
            gxx = config.find_program("c++")
        if gxx is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cxx", cmd=str(gxx.path))
        if gxx.version:
            tool_config.version = gxx.version
        return tool_config


class GccArchiver(BaseTool):
    """GNU archiver tool for creating static libraries.

    Variables:
        cmd: Archiver command (default: 'ar')
        flags: Archiver flags (default: 'rcs')
        libcmd: Command template for creating static library
    """

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "ar",
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

        ar = config.find_program("ar")
        if ar is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ar", cmd=str(ar.path))


class GccLinker(BaseTool):
    """GCC linker tool.

    Variables:
        cmd: Linker command (default: 'gcc')
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
            "cmd": "gcc",
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

        gcc = config.find_program("gcc")
        if gcc is None:
            gcc = config.find_program("cc")
        if gcc is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(gcc.path))


def _build_scan_node(
    project: Project,
    src: Path,
    obj_node: FileNode,
    compile_flags: list[str],
    compiler_cmd: str,
    build_dir: Path,
    modules_flag: str,
) -> FileNode:
    """Generate the scan target and its build information."""
    obj_path = obj_node.path
    scan_path = obj_path.with_suffix(obj_path.suffix + ".scan")
    depfile_path = scan_path.with_suffix(scan_path.suffix + ".d")
    scan_node = FileNode(str(scan_path), defined_at=obj_node.defined_at)

    # Filter out -fmodules from scan (GCC emits extra make-rules that Ninja rejects)
    scan_flags = [f for f in compile_flags if f != modules_flag]

    def ninja_relativize(path: str) -> str:
        """Convert project-relative path to topdir-relative."""
        return f"$topdir/{path}"

    normalized_flags: list[str] = []
    for f in scan_flags:
        if f.startswith("-I") and len(f) > 2:
            inc = f[2:]
            if not inc.startswith("$"):
                inc_path = project._path_resolver.make_project_relative(Path(inc))
                token = PathToken(
                    prefix="-I",
                    path=str(inc_path),
                    path_type="project",
                )
                normalized_flags.append(token.relativize(ninja_relativize))
            else:
                normalized_flags.append(f)
        else:
            normalized_flags.append(f)

    # Paths for command execution
    scan_rel = str(scan_path.relative_to(build_dir)).replace("\\", "/")
    depfile_rel = str(depfile_path.relative_to(build_dir)).replace("\\", "/")
    rel_src = src
    if hasattr(project, "_path_resolver"):
        rel_src = project._path_resolver.make_project_relative(src)
        if not rel_src.startswith("../") and not rel_src.startswith("./"):
            rel_src = f"$topdir/{rel_src}"

    # Build scan command — two steps: generate depfile, then create stamp file.
    # On Windows, Ninja spawns processes via CreateProcess without a shell, so
    # && and touch are not available.  Wrap with cmd /c and use "type nul >"
    # as the cross-platform stamp-creation equivalent.
    platform_info = get_platform()
    flags_str = " ".join(normalized_flags)
    if platform_info.is_windows:
        # Back-slash the stamp path for cmd.exe
        scan_rel_win = scan_rel.replace("/", "\\")
        scan_cmd = (
            f'cmd /c "{compiler_cmd} -MM -MT {scan_rel} -MF {depfile_rel}'
            f" {flags_str} {rel_src}"
            f' && type nul > {scan_rel_win}"'
        )
    else:
        scan_cmd = (
            f"{compiler_cmd} -MM -MT {scan_rel} -MF {depfile_rel}"
            f" {flags_str} {rel_src} && touch {scan_rel}"
        )

    from pcons.core.node import BuildInfo

    scan_node._build_info = BuildInfo(
        tool="cxx_scan",
        command=scan_cmd,
        sources=[project.node(src)],
        depfile=PathToken(suffix=".d"),
        deps_style="gcc",
        description=f"SCAN {src}",
    )

    return scan_node


class GccToolchain(UnixToolchain):
    """GCC toolchain for C and C++ development.

    Includes: C compiler (gcc), C++ compiler (g++), archiver (ar), linker (gcc/g++)

    Inherits from UnixToolchain which provides:
    - get_source_handler() for C/C++/Objective-C/assembly files
    - get_object_suffix(), get_static_library_name(), etc.
    - get_compile_flags_for_target_type() for -fPIC handling
    - get_separated_arg_flags() for flags like -arch, -framework, etc.
    - apply_target_arch() for macOS cross-compilation
    - apply_variant() for debug/release/etc. configurations
    """

    TOOL_NAMES = ("cc", "cxx", "ar", "link")

    def __init__(self) -> None:
        super().__init__("gcc")

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, including C++20 module interfaces."""
        from pcons.tools.toolchain import CXX_MODULE_INTERFACE_SUFFIXES

        handler = super().get_source_handler(suffix)
        if handler is not None:
            return handler

        if suffix in CXX_MODULE_INTERFACE_SUFFIXES:
            return SourceHandler(
                "cxx", "cxx_module", ".o", TargetPath(suffix=".d"), "gcc"
            )

        return None

    def after_resolve(
        self,
        project: Project,
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]],
    ) -> None:
        """Configure `import std;` / `import std.compat;` support for GCC.

        Triggered when module scanning is enabled — either implicitly (the
        env has a C++ module-interface source such as ``.cppm``) or
        explicitly (``env.cxx.modules = True``). The method:

            1. Uses GCC's p1689 scanner mode (``-fdeps-format=p1689r5``)
                to discover module provides/requires for each TU.
            2. Locates the corresponding ``bits/std.cc`` /
                ``bits/std.compat.cc`` source via the preprocessor.
            3. Synthesizes a build node that compiles the std module with
                ``-fmodules``.  GCC automatically places the resulting
                ``std.gcm`` in ``gcm.cache/`` relative to the build dir,
                where Ninja runs.
            4. Adds ``-fmodules`` to every qualifying C++ TU.
            5. Writes Ninja dyndep from the scan results and attaches it to
                all module-participating object nodes.
            6. Wires the std object into link inputs of importing targets.

        Requires GCC 15+ (which ships ``bits/std.cc`` as part of libstdc++).
        """
        from pcons.toolchains.cxx_module_scanner import (
            TuScanSpec,
            _write_text_if_changed,
            bmi_key_for_flags,
            module_file_for,
            scan_translation_units,
            select_modules_scope,
            wire_std_into_targets,
            write_dyndep_entries,
        )

        flag_spec = _gcc_std_module_flag_spec()

        cxx_module_pairs, cxx_pairs = select_modules_scope(source_obj_by_language)
        all_cxx_pairs = cxx_module_pairs + cxx_pairs
        if not all_cxx_pairs:
            return

        build_dir = project.build_dir
        moddir = "cxx_modules"
        dyndep_path = build_dir / "cxx_modules.dyndep"
        dyndep_rel = "cxx_modules.dyndep"
        (build_dir / moddir).mkdir(parents=True, exist_ok=True)

        first_env = None
        _, first_obj = all_cxx_pairs[0]
        build_info = getattr(first_obj, "_build_info", None)
        if build_info:
            first_env = build_info.get("env")

        cxx_tool = getattr(first_env, "cxx", None) if first_env else None
        compiler_cmd = str(getattr(cxx_tool, "cmd", "g++") or "g++")
        base_flags = list(getattr(cxx_tool, "flags", None) or [])
        module_src_paths = {src for src, _ in cxx_module_pairs}

        # Enable modules for all participating C++ TUs.
        modules_flag = "-fmodules"
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi is None:
                continue
            context = bi.get("context")
            if context is not None and hasattr(context, "flags"):
                if modules_flag not in context.flags:
                    context.flags.append(modules_flag)
            # Keep header depfiles for regular C++ TUs. For module interfaces,
            # let dyndep drive module dependencies to avoid depfile conflicts.
            if src in module_src_paths:
                bi["deps_style"] = None
                bi["depfile"] = None

        # Build per-TU scan specs and run GCC p1689 scanning.
        # obj_key maps each participating object node to the hash of its
        # BMI-sensitive flags. TUs sharing a key may share one compiled
        # module interface under cxx_modules/<key>/; TUs with different
        # keys (e.g. -std=c++23 vs -std=c++26) get separate BMIs.
        specs: list[TuScanSpec] = []
        spec_to_obj: dict[int, FileNode] = {}
        obj_key: dict[int, str] = {}
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            context = bi.get("context") if bi else None
            seen: set[str] = set(base_flags)
            compile_flags = list(base_flags)
            if modules_flag not in seen:
                compile_flags.append(modules_flag)
                seen.add(modules_flag)
            if context:
                for f in context.flags:
                    if f not in seen:
                        compile_flags.append(f)
                        seen.add(f)
                for inc in context.includes:
                    compile_flags.append(f"-I{inc}")
                for d in context.defines:
                    compile_flags.append(f"-D{d}")

            # For module interfaces, insert a scan step to generate the depfile.
            if src in module_src_paths:
                scan_node = _build_scan_node(
                    project,
                    src,
                    obj_node,
                    compile_flags,
                    compiler_cmd,
                    build_dir,
                    modules_flag,
                )

                if first_env is not None:
                    first_env.register_node(scan_node)
                obj_node.implicit_deps.append(scan_node)

            spec = TuScanSpec(
                src=src.resolve(),
                obj_rel=str(obj_node.path.relative_to(build_dir)).replace("\\", "/"),
                compiler=compiler_cmd,
                compile_flags=compile_flags,
            )
            specs.append(spec)
            spec_to_obj[id(spec)] = obj_node
            obj_key[id(obj_node)] = bmi_key_for_flags(compile_flags, flag_spec)

        results = scan_translation_units(
            specs, scanner=compiler_cmd, scanner_style="gcc"
        )

        required_logical_names: set[str] = set()
        for r in results:
            required_logical_names.update(r.required_logical_names)
        std_wanted = required_logical_names & {"std", "std.compat"}

        std_obj_nodes = self._inject_gcc_std_module_builds(
            project,
            build_dir,
            moddir,
            compiler_cmd,
            base_flags,
            std_wanted,
            first_env,
            cxx_tool,
        )

        # Scan synthesized std module sources too, so dyndep can capture
        # std/std.compat provides/requires relationships accurately.
        if std_obj_nodes:
            std_specs: list[TuScanSpec] = []
            for _logical, std_obj_node in std_obj_nodes.items():
                std_bi = std_obj_node._build_info
                assert std_bi is not None
                std_src = std_bi["sources"][0].path
                std_obj_rel = str(std_obj_node.path.relative_to(build_dir)).replace(
                    "\\", "/"
                )
                std_spec = TuScanSpec(
                    src=std_src,
                    obj_rel=std_obj_rel,
                    compiler=compiler_cmd,
                    compile_flags=[*base_flags, modules_flag],
                )
                std_specs.append(std_spec)
                spec_to_obj[id(std_spec)] = std_obj_node
                obj_key[id(std_obj_node)] = bmi_key_for_flags(
                    std_spec.compile_flags, flag_spec
                )

            results.extend(
                scan_translation_units(
                    std_specs,
                    scanner=compiler_cmd,
                    scanner_style="gcc",
                )
            )

        # Map every module provider to a BMI path under its key's directory,
        # then write a GCC module mapper file per key. Module interfaces no
        # longer land in the shared gcm.cache/; each compatibility class owns
        # cxx_modules/<key>/<module>.gcm, so the same logical module compiled
        # with incompatible flags never collides on a single output path.
        def keyed_bmi(logical: str, key: str) -> str:
            return module_file_for(logical, f"{moddir}/{key}", ".gcm")

        key_to_modules: dict[str, dict[str, str]] = {}
        provider_obj: dict[tuple[str, str], str] = {}
        for r in results:
            if not r.is_module_provider:
                continue
            obj_node = spec_to_obj[id(r.spec)]
            key = obj_key[id(obj_node)]
            slot = (key, r.logical_name)
            if slot in provider_obj and provider_obj[slot] != r.spec.obj_rel:
                raise RuntimeError(
                    f"Module '{r.logical_name}' is compiled into two different "
                    f"objects ({provider_obj[slot]} and {r.spec.obj_rel}) with "
                    f"BMI-equivalent flags, so both would write the same "
                    f"{keyed_bmi(r.logical_name, key)}. Give them distinct "
                    f"BMI-sensitive flags or build the interface in one place."
                )
            provider_obj[slot] = r.spec.obj_rel
            key_to_modules.setdefault(key, {})[r.logical_name] = keyed_bmi(
                r.logical_name, key
            )

        mapper_flag_for_key: dict[str, str] = {}
        for key, modules in key_to_modules.items():
            (build_dir / moddir / key).mkdir(parents=True, exist_ok=True)
            mapper_rel = f"{moddir}/{key}/modules.modmap"
            lines = ["$root ."]
            for logical in sorted(modules):
                lines.append(f"{logical} {modules[logical]}")
            _write_text_if_changed(build_dir / mapper_rel, "\n".join(lines) + "\n")
            mapper_flag_for_key[key] = f"-fmodule-mapper={mapper_rel}"

        # Build a keyed dyndep: each TU's provides/requires resolve to BMIs in
        # its own compatibility class's directory.
        entries: list[tuple[str, list[str], list[str]]] = []
        for r in results:
            obj_node = spec_to_obj[id(r.spec)]
            key = obj_key[id(obj_node)]
            provides: list[str] = []
            if r.is_module_provider:
                provides.append(keyed_bmi(r.logical_name, key))
            requires: list[str] = [
                keyed_bmi(ln, key)
                for ln in r.required_logical_names
                if (key, ln) in provider_obj
            ]
            entries.append((r.spec.obj_rel, provides, requires))
        write_dyndep_entries(entries, dyndep_path)
        logger.debug("Wrote GCC C++ module dyndep to %s", dyndep_path)

        dyndep_node = project.node(dyndep_path)
        for src, obj_node in all_cxx_pairs:
            bi = getattr(obj_node, "_build_info", None)
            if bi is not None:
                bi["dyndep"] = dyndep_rel
                extra = bi.setdefault("extra_command_flags", [])
                mapper_flag = mapper_flag_for_key.get(obj_key[id(obj_node)])
                if mapper_flag and mapper_flag not in extra:
                    extra.append(mapper_flag)
                if src not in module_src_paths and "-Mno-modules" not in extra:
                    extra.append("-Mno-modules")
            if dyndep_node not in obj_node.implicit_deps:
                obj_node.implicit_deps.append(dyndep_node)
        for std_obj_node in std_obj_nodes.values():
            std_bi = std_obj_node._build_info
            assert std_bi is not None
            std_bi["dyndep"] = dyndep_rel
            extra = std_bi.setdefault("extra_command_flags", [])
            mapper_flag = mapper_flag_for_key.get(obj_key[id(std_obj_node)])
            if mapper_flag and mapper_flag not in extra:
                extra.append(mapper_flag)
            if dyndep_node not in std_obj_node.implicit_deps:
                std_obj_node.implicit_deps.append(dyndep_node)

        if std_obj_nodes:
            wire_std_into_targets(project, results, spec_to_obj, std_obj_nodes)

    def _inject_gcc_std_module_builds(
        self,
        project: Project,
        build_dir: Path,
        moddir: str,
        compiler_cmd: str,
        base_flags: list[str],
        wanted: set[str],
        first_env: Environment | None,
        cxx_tool: Any,
    ) -> dict[str, FileNode]:
        """Synthesize build nodes for ``import std;`` / ``import std.compat;`` (GCC).

        For each logical module name in *wanted* (``"std"`` or
        ``"std.compat"``):

        * Locates the corresponding libstdc++ source via the preprocessor.
        * Creates a build node that compiles it with ``-fmodules`` and the
          user's ABI-affecting flags.  GCC automatically writes
          ``gcm.cache/<logical>.gcm`` next to the build directory CWD.

        Returns a ``{logical_name: obj_node}`` dict for the modules that
        were successfully synthesized.
        """
        from pcons.toolchains.cxx_module_scanner import (
            select_std_module_flags,
        )

        if not wanted:
            return {}

        # Carry ABI-affecting flags onto the std-module compile.
        env_defines = list(getattr(cxx_tool, "defines", None) or [])
        dprefix = str(getattr(cxx_tool, "dprefix", "-D") or "-D")
        all_user_flags = list(base_flags) + [f"{dprefix}{d}" for d in env_defines]

        passthrough = select_std_module_flags(
            all_user_flags, _gcc_std_module_flag_spec()
        )
        if not any(f.startswith("-std=") for f in passthrough):
            passthrough.insert(0, "-std=c++23")

        std_obj_nodes: dict[str, FileNode] = {}
        for logical in sorted(wanted):
            src_path = _find_gcc_std_module_source(compiler_cmd, logical, base_flags)
            if src_path is None:
                raise RuntimeError(
                    f"`import {logical};` was used, but pcons could not locate "
                    f"the GCC standard-library module source. Tried resolving "
                    f"'bits/{'std.cc' if logical == 'std' else 'std.compat.cc'}' "
                    f"via GCC include tracing:\n"
                    f"    {compiler_cmd} ... -E -x c++ - -H  (with #include <bits/...>)\n"
                    f"Requires GCC 15+ with libstdc++ headers installed. "
                    f"On Ubuntu/Debian: apt install gcc g++ libstdc++-15-dev"
                )

            obj_rel = f"{moddir}/{logical}.o"
            obj_path = build_dir / obj_rel

            std_obj_node = project.node(obj_path)
            cmd_list: list[str] = [
                compiler_cmd,
                *passthrough,
                "-fmodules",
                "-x",
                "c++",
                str(src_path),
                "-c",
                "-o",
                obj_rel,
            ]
            std_obj_node._build_info = {
                "tool": "cxx",
                "command_var": "stdmodcmd",
                "description": f"CXX {logical} module",
                "sources": [project.node(src_path)],
                "command": cmd_list,
            }
            if first_env is not None:
                first_env.register_node(std_obj_node)

            std_obj_nodes[logical] = std_obj_node

        return std_obj_nodes

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        cc = GccCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = GccCxxCompiler()
        cxx.configure(config)

        ar = GccArchiver()
        ar.configure(config)

        link = GccLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}
        return True


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import SourceHandler, toolchain_registry  # noqa: E402


def _gcc_is_available() -> bool:
    """Check whether a *real* GCC is available as ``gcc``.

    On Github-hosted macOS runners, gcc is a shim to apple-clang,
    we should not treat it as a real GCC.
    """
    gcc = shutil.which("gcc")
    if gcc is None:
        return False

    try:
        result = subprocess.run(
            [gcc, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        # assume it's usable for now
        return True

    # On Github-hosted macOS runners, gcc is a shim to apple-clang, let refuse it.
    return "clang" not in result.stdout.lower()


toolchain_registry.register(
    GccToolchain,
    aliases=["gcc", "gnu"],
    check_command="gcc",
    tool_classes=[GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker],
    category="c",
    platforms=["linux", "darwin", "win32"],
    description="GNU Compiler Collection (gcc/g++)",
    finder="find_c_toolchain()",
    is_available=_gcc_is_available,
)
