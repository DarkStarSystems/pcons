# SPDX-License-Identifier: MIT
"""GCC toolchain: gcc, g++, ar, and gcc/g++ as linker."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.node import FileNode
from pcons.core.subst import TargetPath
from pcons.toolchains.gnu_common import (
    gnu_archiver_builders,
    gnu_archiver_vars,
    gnu_compile_builders,
    gnu_compile_vars,
    gnu_link_builders,
    gnu_link_vars,
)
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
    """GCC C compiler tool (variables come from gnu_compile_vars)."""

    env_var = "CC"

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars("gcc", "cc")

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cc")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "gcc", "cc", with_version=True)


class GccCxxCompiler(BaseTool):
    """GCC C++ compiler tool (variables come from gnu_compile_vars)."""

    env_var = "CXX"

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        base = gnu_compile_vars("g++", "cxx")
        objcmd = list(cast("list[object]", base["objcmd"]))
        # Scanned TUs compile with a reference to their collate-written GCC
        # module-mapper file ($CXX_MODMAPREF is a per-edge variable, so one
        # rule serves every TU in a flag class). The mapper flag is
        # position-independent, so it rides at the end. Non-module TUs keep
        # their header depfiles and add -Mno-modules so those depfiles skip
        # BMI entries; module interface units drop the depflags entirely —
        # GCC's depfile there names the BMI as both target and prerequisite,
        # which ninja reads as a cycle (#102); their header tracking comes
        # from an implicit dep on their own scan output instead.
        modobjcmd = objcmd + ["$CXX_MODMAPREF", "-Mno-modules"]
        modifacecmd = [t for t in objcmd if t != "$cxx.depflags"] + ["$CXX_MODMAPREF"]
        return {
            **base,
            "modobjcmd": modobjcmd,
            "modifacecmd": modifacecmd,
            # None = auto (module-suffix sources opt the env in); True also
            # scans module units in .cpp files; False disables scanning.
            "modules": None,
        }

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cxx")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "g++", "c++", with_version=True)


class GccArchiver(BaseTool):
    """GNU archiver (ar) for creating static libraries."""

    env_var = "AR"

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return gnu_archiver_vars("ar")

    def builders(self) -> dict[str, Builder]:
        return gnu_archiver_builders()

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "ar")


class GccLinker(BaseTool):
    """GCC linker tool (variables come from gnu_link_vars)."""

    env_var = "CC"

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return gnu_link_vars("gcc")

    def builders(self) -> dict[str, Builder]:
        return gnu_link_builders()

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "gcc", "cc")


class GccToolchain(UnixToolchain):
    """GCC toolchain: gcc, g++, ar, and gcc/g++ as linker.

    Source handling, naming conventions, arch/variant handling come from
    UnixToolchain.
    """

    ENV_COMPILER_FAMILY = "gcc"

    TOOL_NAMES = ("cc", "cxx", "ar", "link")

    def __init__(self) -> None:
        super().__init__("gcc")

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross preset, requiring cross binaries for foreign triples.

        GCC rejects --target=; a different-triple build needs different tool
        binaries, so a preset with a triple but no CC/CXX overrides fails
        fast rather than silently building host-arch objects.
        """
        triple = getattr(preset, "triple", None)
        resolve = getattr(preset, "resolved_tool_cmds", None)
        cmds = resolve() if resolve is not None else {}
        # wasm triples get the more specific "use the dedicated toolchain"
        # error from UnixToolchain (via super()), not the binary-retarget one.
        if triple and str(triple).startswith("wasm32"):
            triple = None
        if triple and not ("cc" in cmds or "cxx" in cmds):
            name = getattr(preset, "name", preset)
            raise ValueError(
                f"Cross preset '{name}' targets triple '{triple}', but GCC "
                f"selects targets by binary, not by flag. Provide cross-compiler "
                f"commands in the preset's tool_cmds (e.g. cc={triple}-gcc), or "
                f"use a clang-based toolchain, which retargets via --target."
            )
        super().apply_cross_preset(env, preset)

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
        """Set up C++20 module compilation (GCC) via the Scanner.

        Configure records static facts only: which targets are scanned,
        each TU's compile flags and BMI-compatibility key, the per-scope
        BMI directory, and ``-fmodules`` on every participating compile.
        Content-derived facts — who provides or imports what, and the
        module-mapper entries GCC needs (its mapper replaces clang's
        ``-fmodule-output``/``-fmodule-file`` flags) — flow through per-TU
        scan edges and a per-target collate at build time, reaching each
        compile as a collate-written per-object mapper file.

        Module interface units compile without a depfile (GCC's names the
        BMI as both target and prerequisite there, a ninja cycle — #102);
        their header tracking is an implicit dep on their own scan output,
        whose depfile covers the same reads.
        """
        from pcons.core.scan import EdgeArgsSpec, Scanner, scope_id_for
        from pcons.core.subst import NodeVar, SourcePath, TargetPath
        from pcons.core.target import Target
        from pcons.toolchains.cxx_module_scanner import (
            bmi_key_for_flags,
            collect_module_scopes,
            merge_scan_compile_flags,
        )

        scopes = collect_module_scopes(project, source_obj_by_language, self)
        if not scopes:
            return
        flag_spec = _gcc_std_module_flag_spec()
        rel = project.top_path_resolver.make_execution_relative

        edge_facts: dict[int, dict[str, object]] = {}
        by_compiler: dict[str, list[object]] = {}
        envs: dict[int, Any] = {}
        env_keys: dict[int, set[str]] = {}
        target_keys: dict[int, set[str]] = {}

        for scope in scopes:
            env = scope.env
            envs[id(env)] = env
            cxx = getattr(env, "cxx", None)
            compiler = str(getattr(cxx, "cmd", "g++") or "g++")
            base_flags = list(getattr(cxx, "flags", None) or [])
            for _src, obj_node, is_module_suffix in scope.pairs:
                bi = obj_node._build_info
                if bi is None:
                    continue
                context = bi.get("context")
                if (
                    context is not None
                    and hasattr(context, "flags")
                    and "-fmodules" not in context.flags
                ):
                    context.flags.append("-fmodules")
                flags = merge_scan_compile_flags(
                    base_flags,
                    context,
                    extra_flags=("-fmodules",),
                    root=project.root_dir,
                )
                obj_rel = rel(obj_node.path)
                key = bmi_key_for_flags(flags, flag_spec)
                edge_facts[id(obj_node)] = {
                    "flags": flags,
                    "key": key,
                    "module_suffix": is_module_suffix,
                    "obj_rel": obj_rel,
                }
                env_keys.setdefault(id(env), set()).add(key)
                target_keys.setdefault(id(scope.target), set()).add(key)

                node_vars = bi.get("vars")
                if node_vars is None:
                    node_vars = {}
                    bi["vars"] = node_vars
                node_vars["CXX_MODMAPREF"] = f"-fmodule-mapper={obj_rel}.modmap"
                if is_module_suffix:
                    bi["command_var"] = "modifacecmd"
                    bi["depfile"] = None
                    bi["deps_style"] = None
                    # Header tracking without a compile depfile (#102): the
                    # scan output's mtime moves exactly when this TU's
                    # source or an included header changed.
                    ddi_node = project.node(
                        obj_node.path.with_name(obj_node.path.name + ".ddi")
                    )
                    if ddi_node not in obj_node.implicit_deps:
                        obj_node.implicit_deps.append(ddi_node)
                else:
                    bi["command_var"] = "modobjcmd"
            by_compiler.setdefault(compiler, []).append(scope.target)

        # Dormant `import std;` edges per BMI key in use.
        std_exports: dict[int, dict[str, str]] = {}
        std_errors: dict[int, str | None] = {}
        for env_id, env in envs.items():
            exports_by_key, error = self._setup_std_modules(
                project, env, env_keys.get(env_id, set()), flag_spec
            )
            std_exports[env_id] = exports_by_key
            std_errors[env_id] = error

        def scan_vars(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            facts = edge_facts[id(governed)]
            return {
                "SCAN_FLAGS": list(cast("list[str]", facts["flags"])),
                # -fdeps-target= takes the joined form only, so the whole
                # flag rides the variable.
                "SCAN_DEPS_TARGET": f"-fdeps-target={facts['obj_rel']}",
            }

        def edge_extra(
            env: object, scanned: list[FileNode], governed: FileNode
        ) -> dict[str, object]:
            facts = edge_facts[id(governed)]
            return {
                "key": facts["key"],
                "module_suffix": facts["module_suffix"],
            }

        def manifest_extra(env: object, target: object) -> dict[str, object]:
            by_key = std_exports.get(id(env), {})
            keys = target_keys.get(id(target), set())
            extra: dict[str, object] = {
                "style": "gcc",
                "bmi_ext": ".gcm",
                "moddir": f"cxx_modules/{scope_id_for(cast(Target, target))}",
                "std_exports": sorted(by_key[k] for k in keys if k in by_key),
            }
            error = std_errors.get(id(env))
            if error:
                extra["std_error"] = error
            return extra

        cxx_suffixes = tuple(
            sorted(
                suffix
                for suffix in self.source_suffixes()
                if (handler := self.get_source_handler(suffix)) is not None
                and handler.language in ("cxx", "cxx_module")
            )
        )

        for compiler, targets in by_compiler.items():
            scanner = Scanner(
                "cxx-modules",
                source_suffixes=cxx_suffixes,
                # GCC scans with the compiler itself: preprocess-only with
                # p1689 output. -fdirectives-only skips macro expansion (the
                # scan wants module declarations, not text) and -o devnull
                # discards the preprocessed output.
                scan_command=[
                    compiler,
                    NodeVar("SCAN_FLAGS"),
                    "-E",
                    "-x",
                    "c++",
                    SourcePath(),
                    "-MT",
                    TargetPath(),
                    "-MD",
                    "-MF",
                    TargetPath(suffix=".d"),
                    "-fmodules",
                    TargetPath(prefix="-fdeps-file="),
                    NodeVar("SCAN_DEPS_TARGET"),
                    "-fdeps-format=p1689r5",
                    "-fdirectives-only",
                    "-o",
                    os.devnull,
                ],
                info_suffix=".ddi",
                scan_depfile=".d",
                scan_deps_style="gcc",
                scan_vars=scan_vars,
                edge_extra=edge_extra,
                manifest_extra=manifest_extra,
                collate_command=[
                    sys.executable,
                    "-m",
                    "pcons.toolchains.cxx_collate",
                    "--manifest",
                    NodeVar("SCAN_MANIFEST"),
                ],
                # The mapper reference lives in the modobjcmd/modifacecmd
                # templates as a per-edge variable; nothing is appended.
                edge_args=EdgeArgsSpec(suffix=".modmap", var=None, token=None),
                # Extra link inputs collate discovers (the std module's
                # object): a response file the gcc driver expands itself.
                link_args=EdgeArgsSpec(
                    suffix=".linkextras.rsp",
                    var="CXX_LINKEXTRAS",
                    token="@$CXX_LINKEXTRAS",
                ),
                link_args_target_types=("program", "shared_library"),
            )
            scanner.attach(*cast("list[Target]", targets))

    def _setup_std_modules(
        self,
        project: Project,
        env: Any,
        keys: set[str],
        flag_spec: Any,
    ) -> tuple[dict[str, str], str | None]:
        """Describe dormant `import std;` build edges for *keys* (GCC).

        Same contract as the LLVM version: nothing depends on these edges
        statically — a TU's collate discovering a real `import std;` is what
        makes ninja build them. GCC writes the BMI where its module mapper
        says, so the std edges carry a small configure-written mapper of
        their own instead of clang's ``-fmodule-output``.

        Returns ``(exports_by_key, error_text)``.
        """
        from pcons.core.collate import write_text_if_changed
        from pcons.toolchains.cxx_module_scanner import (
            bmi_key_for_flags,
            select_std_module_flags,
        )

        cxx = getattr(env, "cxx", None)
        compiler_cmd = str(getattr(cxx, "cmd", "g++") or "g++")
        base_flags = list(getattr(cxx, "flags", None) or [])

        env_defines = list(getattr(cxx, "defines", None) or [])
        dprefix = str(getattr(cxx, "dprefix", "-D") or "-D")
        all_user_flags = list(base_flags) + [f"{dprefix}{d}" for d in env_defines]
        passthrough = select_std_module_flags(all_user_flags, flag_spec)
        if not any(f.startswith("-std=") for f in passthrough):
            passthrough.insert(0, "-std=c++23")

        std_key = bmi_key_for_flags([*passthrough, "-fmodules"], flag_spec)
        if std_key not in keys:
            return {}, None

        # Like the LLVM and MSVC versions: a missing module source skips
        # that one module with a warning, and only losing *std itself*
        # disables the mechanism — std.compat is unusable without it anyway,
        # and an error about a module the project never imports helps no one.
        sources: dict[str, Path] = {}
        for logical in ("std", "std.compat"):
            src_path = _find_gcc_std_module_source(compiler_cmd, logical, base_flags)
            if src_path is None:
                logger.warning(
                    "GCC standard-library module source for %s not found; "
                    "skipping that module",
                    logical,
                )
            else:
                sources[logical] = src_path
        if "std" not in sources:
            return {}, (
                f"`import std;` needs the GCC standard-library module "
                f"source, which pcons could not locate via GCC include "
                f"tracing:\n"
                f"    {compiler_cmd} ... -E -x c++ - -H  "
                f"(with #include <bits/...>)\n"
                f"Requires GCC 15+ with libstdc++ headers installed. "
                f"On Ubuntu/Debian: apt install gcc g++ libstdc++-15-dev"
            )

        build_dir = project.build_dir
        build_dir_fs = (
            build_dir if build_dir.is_absolute() else project.root_dir / build_dir
        )
        std_moddir = f"cxx_modules/std/{std_key}"
        (build_dir_fs / std_moddir).mkdir(parents=True, exist_ok=True)

        # GCC learns both where to write a provided BMI and where to read an
        # imported one from the mapper; one static file serves both edges.
        mapper_rel = f"{std_moddir}/std.modmap"
        mapper_lines = ["$root ."]
        for logical in sources:
            mapper_lines.append(f"{logical} {std_moddir}/{logical}.gcm")
        write_text_if_changed(build_dir_fs / mapper_rel, "\n".join(mapper_lines) + "\n")

        exports_modules: dict[str, dict[str, object]] = {}
        prev_pcm_node: FileNode | None = None
        for logical in ("std", "std.compat"):
            src_path = sources.get(logical)
            if src_path is None:
                continue
            gcm_rel = f"{std_moddir}/{logical}.gcm"
            obj_rel = f"{std_moddir}/{logical}.o"
            std_obj_node = project.node(build_dir / obj_rel)
            gcm_node = project.node(build_dir / gcm_rel)
            cmd_list: list[str] = [
                compiler_cmd,
                *passthrough,
                "-fmodules",
                f"-fmodule-mapper={mapper_rel}",
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
                "outputs": {
                    "obj": {"path": std_obj_node.path, "implicit": False},
                    "gcm": {"path": gcm_node.path, "implicit": True},
                },
            }
            gcm_node._build_info = {"primary_node": std_obj_node}
            if prev_pcm_node is not None:
                std_obj_node.depends(prev_pcm_node)
            env.register_node(std_obj_node)
            env.register_node(gcm_node)
            prev_pcm_node = gcm_node

            exports_modules[logical] = {
                "bmi": gcm_rel,
                "key": std_key,
                "obj": obj_rel,
                "is_interface": True,
                "requires": ["std"] if logical == "std.compat" else [],
            }

        exports_rel = f"cxx_modules/std/{std_key}.exports.json"
        write_text_if_changed(
            build_dir_fs / exports_rel,
            json.dumps(
                {
                    "version": 1,
                    "scanner": "cxx-modules",
                    "scope": f"std/{std_key}",
                    "modules": exports_modules,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n",
        )
        return {std_key: exports_rel}, None

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

    On macOS (e.g. GitHub-hosted runners), ``gcc`` is often a shim for
    apple-clang; refuse those.
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
