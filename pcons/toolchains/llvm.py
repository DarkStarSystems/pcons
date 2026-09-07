# SPDX-License-Identifier: MIT
"""LLVM/Clang toolchain implementation."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.builder_registry import builder
from pcons.core.subst import SourcePath, TargetPath
from pcons.core.target import Target
from pcons.toolchains.gnu_common import (
    gnu_archiver_builders,
    gnu_archiver_vars,
    gnu_compile_builders,
    gnu_compile_vars,
    gnu_link_builders,
    gnu_link_vars,
)
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.compile_link import CompileLinkFactory
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import CXX_MODULE_INTERFACE_SUFFIXES
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.configure.platform import Platform
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode, Node
    from pcons.core.project import Project
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)


# File names we ask `clang -print-file-name` to resolve, in priority order.
# The bare name is the modern layout (LLVM ≥ 19, e.g. Homebrew, Debian/Ubuntu,
# and Arch, which drop the manifest straight into the library dir like
# `/usr/lib/libc++.modules.json`). The `c++/`-prefixed name is the older layout
# kept for backward compatibility. Single source of truth so the lookup and the
# not-found error message can't drift apart.
_LIBCXX_MANIFEST_NAMES = (
    "libc++.modules.json",
    "c++/libc++.modules.json",
)


def _stdlib_query_flags(base_flags: list[str]) -> list[str]:
    """The -stdlib flags the manifest lookup queries with.

    The user's own when they chose one, else `-stdlib=libc++` — the library
    that ships the manifest. Shared by the lookup and its not-found error,
    so the reproduction commands the error prints are the ones actually run.
    """
    user_stdlib_flags = [f for f in base_flags if f.startswith("-stdlib=")]
    return user_stdlib_flags or ["-stdlib=libc++"]


def _find_libcxx_modules_manifest(
    compiler_cmd: str, base_flags: list[str]
) -> Path | None:
    """Locate `libc++.modules.json` via `clang -print-file-name`.

    libc++ ships a JSON manifest that points at `std.cppm` /
    `std.compat.cppm` and the system include directories required to
    compile them. We let the compiler tell us where it is — works for any
    libc++ install (Homebrew, apt, Arch, vendored). We try both the modern
    and legacy manifest layouts (see `_LIBCXX_MANIFEST_NAMES`).

    Returns the manifest path if found, or None if the toolchain doesn't
    ship one (Apple Clang ≤ 21 is the most common case; users need
    Homebrew LLVM there).
    """
    cmd = [compiler_cmd, *_stdlib_query_flags(base_flags)]
    for candidate in _LIBCXX_MANIFEST_NAMES:
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

    env_var = "CC"

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars("clang", "cc")

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cc")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "clang", with_version=True)


class ClangCxxCompiler(BaseTool):
    """Clang C++ compiler tool."""

    env_var = "CXX"

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        base = gnu_compile_vars("clang++", "cxx")
        # A scanned TU compiles with `modobjcmd`: `objcmd` plus a reference
        # to its collate-written modmap response file, placed *before* the
        # source because clang's -x (inside the modmap) applies only to
        # inputs after it. $CXX_MODMAPREF is a per-edge variable ("@<path>"),
        # so every scanned TU still shares one rule per flag class.
        objcmd = list(cast("list[object]", base["objcmd"]))
        modobjcmd = objcmd[:-4] + ["$CXX_MODMAPREF"] + objcmd[-4:]
        return {
            **base,
            "modobjcmd": modobjcmd,
            "moddir": "cxx_modules",
            # None = auto (module-suffix sources opt the env in); True also
            # scans module units in .cpp files; False disables scanning.
            "modules": None,
            "scan_deps": "",  # override the clang-scan-deps executable
        }

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cxx")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "clang++", with_version=True)


class LlvmArchiver(BaseTool):
    """LLVM archiver tool."""

    env_var = "AR"

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        import shutil

        # Prefer llvm-ar if available, otherwise fall back to ar
        ar_cmd = "llvm-ar" if shutil.which("llvm-ar") else "ar"
        return gnu_archiver_vars(ar_cmd)

    def builders(self) -> dict[str, Builder]:
        return gnu_archiver_builders()

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "llvm-ar", "ar")


class LlvmLinker(BaseTool):
    """LLVM linker tool (variables come from gnu_link_vars)."""

    env_var = "CC"

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return gnu_link_vars("clang")

    def builders(self) -> dict[str, Builder]:
        return gnu_link_builders()

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "clang")


#: Target type for a linked .metallib. Not one of the four the shared
#: compile/link step knows how to finish, so it compiles the sources and
#: leaves the link to MetalLibraryFactory, below.
METAL_LIBRARY_TARGET_TYPE = "metal_library"


class MetalCompiler(BaseTool):
    """Apple Metal shader toolchain (macOS only).

    Provides both halves of the Metal pipeline: `Object` compiles .metal
    sources to .air (Apple Intermediate Representation), and `Library` links
    .air files into the .metallib archive an application actually loads.
    """

    def __init__(self) -> None:
        super().__init__("metal", language="metal")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "xcrun",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            # Flags for the metallib step; the compile flags above (-I, -std=...)
            # are not accepted there.
            "libflags": [],
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
            "metallibcmd": [
                "$metal.cmd",
                "metallib",
                "$metal.libflags",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object",
                "metal",
                "metalcmd",
                src_suffixes=[".metal"],
                target_suffixes=[".air"],
                language="metal",
                single_source=True,
            ),
            "Library": CommandBuilder(
                "Library",
                "metal",
                "metallibcmd",
                src_suffixes=[".air"],
                target_suffixes=[".metallib"],
                language="metal",
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None

        platform = get_platform()
        if not platform.is_macos:
            return None

        xcrun = config.find_program("xcrun", version_flag="")
        if xcrun is None:
            return None

        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("metal", cmd=str(xcrun.path))


class LlvmToolchain(UnixToolchain):
    """LLVM/Clang toolchain for C and C++ development.

    Source handling, naming conventions, arch/variant handling come from
    UnixToolchain. Additionally supports Metal shaders on macOS.
    """

    ENV_COMPILER_FAMILY = "llvm"

    TOOL_NAMES = ("cc", "cxx", "ar", "link", "metal")

    # Clang understands --target=<triple> for cross-compilation.
    IS_CLANG_DRIVER = True

    def __init__(self) -> None:
        super().__init__("llvm")

    def get_output_prefix(
        self, target_type: str, target: Platform | None = None
    ) -> str:
        """A .metallib takes its name verbatim; everything else is Unix."""
        if target_type == METAL_LIBRARY_TARGET_TYPE:
            return ""
        return super().get_output_prefix(target_type, target)

    def get_output_suffix(
        self, target_type: str, target: Platform | None = None
    ) -> str:
        if target_type == METAL_LIBRARY_TARGET_TYPE:
            return ".metallib"
        return super().get_output_suffix(target_type, target)

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled.

        Adds C++20 module interfaces and Metal shaders (macOS) to the base
        Unix handlers.
        """
        from pcons.tools.toolchain import SourceHandler

        # Replace the base handler's ".o" with the platform object suffix:
        # Clang on Windows uses MSVC object conventions (".obj").
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

        # Metal shaders (macOS only) compile to .air
        platform = get_platform()
        if suffix.lower() == ".metal" and platform.is_macos:
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

        # Metal compiler is optional (macOS only)
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
        """Set up C++20 module compilation (LLVM/Clang) via the Scanner.

        Configure records only static facts: which targets are scanned
        (suffix/opt-in), each TU's compile flags and BMI-compatibility key,
        and the per-scope BMI directory. Everything content-derived — who
        provides or imports what, the `-x c++-module`/`-fmodule-output`
        flags on providers, the `-fmodule-file=` references — flows through
        per-TU scan edges and a per-target collate at build time
        (pcons.toolchains.cxx_collate), reaching the compile command through
        each TU's collate-written modmap response file.
        """
        from pcons.core.scan import EdgeArgsSpec, Scanner, scope_id_for
        from pcons.core.subst import NodeVar, Verbatim
        from pcons.toolchains.cxx_module_scanner import (
            CLANG_SCAN_DEPS_HINTS,
            bmi_key_for_flags,
            collect_module_scopes,
            find_scan_deps,
            merge_scan_compile_flags,
        )

        scopes = collect_module_scopes(project, source_obj_by_language, self)
        if not scopes:
            return
        flag_spec = _clang_std_module_flag_spec()
        rel = project._path_resolver.make_execution_relative

        # Per-object facts the scanner callbacks read. Keys are id(obj_node);
        # the callbacks run later in the same resolve pass.
        edge_facts: dict[int, dict[str, object]] = {}
        by_tools: dict[tuple[str, str], list[object]] = {}
        envs: dict[int, Any] = {}
        env_keys: dict[int, set[str]] = {}
        target_keys: dict[int, set[str]] = {}

        for scope in scopes:
            env = scope.env
            envs[id(env)] = env
            scan_exe = find_scan_deps(env, ["clang-scan-deps"], CLANG_SCAN_DEPS_HINTS)
            cxx = getattr(env, "cxx", None)
            compiler = str(getattr(cxx, "cmd", "clang++") or "clang++")
            base_flags = list(getattr(cxx, "flags", None) or [])
            for _src, obj_node, is_module_suffix in scope.pairs:
                bi = obj_node._build_info
                if bi is None:
                    continue
                context = bi.get("context")
                # Suffix is a static fact: pre-flag extension-tagged module
                # units so both the scan and the compile see them as modules
                # (clang doesn't recognize .ixx natively). Content-discovered
                # providers in .cpp files get theirs from the modmap.
                if (
                    is_module_suffix
                    and context is not None
                    and hasattr(context, "flags")
                    and "-x" not in context.flags
                ):
                    context.flags.extend(["-x", "c++-module"])
                flags = merge_scan_compile_flags(
                    base_flags, context, root=project.root_dir
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
                # The compile switches to the modmap-aware template; the
                # reference is a per-edge variable so the rule stays shared.
                bi["command_var"] = "modobjcmd"
                node_vars = bi.get("vars")
                if node_vars is None:
                    node_vars = {}
                    bi["vars"] = node_vars
                node_vars["CXX_MODMAPREF"] = "@" + obj_rel + ".modmap"
            by_tools.setdefault((scan_exe, compiler), []).append(scope.target)

        # Dormant `import std;` edges, one set per BMI key in use: described
        # now, built only if some TU's collate actually requires them.
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
            # A list value is quoted token-by-token in the generated build
            # file, so flags with spaces survive; a pre-joined string would
            # be quoted whole, into one argument.
            return {
                "SCAN_FLAGS": list(cast("list[str]", facts["flags"])),
                "SCAN_OBJ": str(facts["obj_rel"]),
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
                "style": "clang",
                "bmi_ext": ".pcm",
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

        for (scan_exe, compiler), targets in by_tools.items():
            scanner = Scanner(
                "cxx-modules",
                source_suffixes=cxx_suffixes,
                # Explicit markers, not "$TARGET" strings: a marker parsed
                # out of "$TARGET.d" becomes a slice, which flips the whole
                # command into indexed-output mode ($target_0) that a plain
                # scan edge never defines.
                scan_command=[
                    scan_exe,
                    "-format=p1689",
                    "--",
                    compiler,
                    NodeVar("SCAN_FLAGS"),
                    SourcePath(),
                    "-c",
                    "-o",
                    NodeVar("SCAN_OBJ"),
                    "-MT",
                    TargetPath(),
                    "-MD",
                    "-MF",
                    TargetPath(suffix=".d"),
                    Verbatim(">"),
                    TargetPath(),
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
                # The modmap reference lives inside `modobjcmd` (it must
                # precede the source file), so no token is appended here.
                edge_args=EdgeArgsSpec(suffix=".modmap", var=None, token=None),
                # Extra link inputs collate discovers (the std module's
                # object): a response file clang expands itself, so it works
                # with every linker clang drives, ld64 included.
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
        """Describe dormant `import std;` build edges for *keys* (clang).

        Nothing depends on these edges statically: they appear in the build
        file and run only when some TU's collate discovers an actual
        `import std;` and its dyndep requires the std BMI. A project that
        never imports std builds nothing here.

        Returns ``(exports_by_key, error_text)``: per-key paths of the
        configure-written std exports files (consumed by cxx_collate like
        any other imports), and, when the toolchain has no std module
        source, the install-hint text collate should show if `import std`
        appears anyway.
        """
        from pcons.core.collate import write_text_if_changed
        from pcons.toolchains.cxx_module_scanner import (
            bmi_key_for_flags,
            select_std_module_flags,
        )

        cxx = getattr(env, "cxx", None)
        compiler_cmd = str(getattr(cxx, "cmd", "clang++") or "clang++")
        base_flags = list(getattr(cxx, "flags", None) or [])

        manifest = _find_libcxx_modules_manifest(compiler_cmd, base_flags)
        if manifest is None:
            stdlib_flags = " ".join(_stdlib_query_flags(base_flags))
            tried = "\n".join(
                f"    {compiler_cmd} {stdlib_flags} -print-file-name={name}"
                for name in _LIBCXX_MANIFEST_NAMES
            )
            return {}, (
                "`import std;` was used, but pcons could not locate libc++'s "
                "C++ standard-library module manifest. Tried both the modern "
                "and legacy layouts:\n"
                f"{tried}\n"
                "and got no usable path. On macOS, install Homebrew LLVM "
                "(`brew install llvm`) — Apple Clang doesn't ship the std "
                "module yet. On Linux, install a recent libc++ that includes "
                "`libc++.modules.json` (LLVM ≥ 18). Alternatively use a "
                "different toolchain (MSVC works on Windows, GCC ≥ 15 works "
                "on Linux)."
            )
        modules = _parse_libcxx_manifest(manifest)

        # ABI-affecting flags from the user's compile flags AND env.cxx
        # defines (where feature-test macros like _LIBCPP_HARDENING_MODE
        # live). The std BMI is keyed by the same BMI-sensitive subset its
        # importers use, so they resolve it under a matching key.
        env_defines = list(getattr(cxx, "defines", None) or [])
        dprefix = str(getattr(cxx, "dprefix", "-D") or "-D")
        all_user_flags = list(base_flags) + [f"{dprefix}{d}" for d in env_defines]
        passthrough = select_std_module_flags(
            all_user_flags, _clang_std_module_flag_spec()
        )
        if not any(f.startswith("-std=") for f in passthrough):
            passthrough.insert(0, "-std=c++20")
        if not any(f.startswith("-stdlib=") for f in passthrough):
            passthrough.append("-stdlib=libc++")

        std_key = bmi_key_for_flags(passthrough, flag_spec)
        if std_key not in keys:
            # No scoped TU shares the std BMI's flag class; an import could
            # never resolve against it, so describing the edges would only
            # add dead lines to the build file.
            return {}, None

        build_dir = project.build_dir
        build_dir_fs = (
            build_dir if build_dir.is_absolute() else project.root_dir / build_dir
        )
        std_moddir = f"cxx_modules/std/{std_key}"
        (build_dir_fs / std_moddir).mkdir(parents=True, exist_ok=True)

        exports_modules: dict[str, dict[str, object]] = {}
        prev_pcm_node: FileNode | None = None
        for logical in ("std", "std.compat"):
            entry = modules.get(logical)
            if entry is None:
                logger.warning(
                    "%s not in libc++ manifest %s; skipping", logical, manifest
                )
                continue
            cppm_path: Path = entry["source-path"]
            sys_includes: list[Path] = entry["system-include-directories"]
            if not cppm_path.is_file():
                logger.warning(
                    "%s: manifest pointed at %s which doesn't exist; skipping",
                    logical,
                    cppm_path,
                )
                continue

            pcm_rel = f"{std_moddir}/{logical}.pcm"
            obj_rel = f"{std_moddir}/{logical}.o"
            std_obj_node = project.node(build_dir / obj_rel)
            pcm_node = project.node(build_dir / pcm_rel)
            cmd_list: list[str] = [
                compiler_cmd,
                *passthrough,
                # `std` starts with a reserved identifier and libc++'s
                # std.cppm uses reserved user-defined literals; both warn
                # under -Werror unless suppressed.
                "-Wno-reserved-module-identifier",
                "-Wno-reserved-identifier",
                "-Wno-reserved-user-defined-literal",
                *(f"-isystem{d}" for d in sys_includes),
                # std.compat imports std; let it find the keyed std.pcm.
                f"-fprebuilt-module-path={std_moddir}",
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
                "outputs": {
                    "obj": {"path": std_obj_node.path, "implicit": False},
                    "pcm": {"path": pcm_node.path, "implicit": True},
                },
            }
            pcm_node._build_info = {"primary_node": std_obj_node}
            if prev_pcm_node is not None:
                std_obj_node.depends(prev_pcm_node)
            env.register_node(std_obj_node)
            env.register_node(pcm_node)
            prev_pcm_node = pcm_node

            exports_modules[logical] = {
                "bmi": pcm_rel,
                "key": std_key,
                "obj": obj_rel,
                "is_interface": True,
                "requires": ["std"] if logical == "std.compat" else [],
            }

        if not exports_modules:
            return {}, None
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


# =============================================================================
# Registration
class MetalLibraryFactory(CompileLinkFactory):
    """Compiles .metal sources to .air, then links them into a .metallib.

    The compile half is the ordinary one — the metal source handler turns
    each .metal into an .air in ``intermediate_nodes``. The shared step then
    finds a target type it doesn't finish and leaves ``output_nodes`` empty,
    so the link lands here, in the toolchain that owns the format.
    """

    def resolve(self, target: Target, env: Environment | None) -> None:
        super().resolve(target, env)
        if env is None or target.output_nodes:
            return
        if not target.intermediate_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        name = self._apply_output_naming(target, env, METAL_LIBRARY_TARGET_TYPE)
        path = target.build_dir / target.path_resolver.normalize_target_path(name)

        node = self.project.node(path)
        node.add_inputs(target.intermediate_nodes)
        # No link context: metallib takes .air files and nothing else — the
        # compile flags (-I, -std=metal3.0) are not accepted here, which is
        # why the tool keeps a separate libflags.
        node._build_info = {
            "tool": "metal",
            "command_var": "metallibcmd",
            "sources": target.intermediate_nodes,
            "env": env,
        }

        target.output_nodes.append(node)
        env.register_node(node)


@builder(
    "MetalLibrary",
    target_type=METAL_LIBRARY_TARGET_TYPE,
    requires_env=True,
    factory_class=MetalLibraryFactory,
    platforms=["darwin"],
    description="Compile .metal shaders and link them into a .metallib",
)
class MetalLibraryBuilder:
    """A .metallib built from .metal sources — the form an application loads.

    `env.metal.Object` / `env.metal.Library` remain available for driving the
    two steps by hand; they return nodes, like every tool-namespace builder.
    This is the Target-returning spelling, so a metallib can be a default
    target, an alias member, or something to Install.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: Sequence[str | Path | Node] | None = None,
        depends: Sequence[Target | Node | Path | str] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a MetalLibrary target.

        Args:
            project: The project to add the target to.
            name: Target name; the output is ``<name>.metallib``.
            env: Environment whose toolchain provides the metal tool.
            sources: ``.metal`` shader sources.
            depends: Extra implicit dependencies (see Program).
            defined_at: Source location where this was defined.

        Example:
            shaders = project.MetalLibrary("myapp", env,
                                           sources=["blur.metal", "warp.metal"])
            project.Default(shaders)
        """
        from pcons.builders.compile import _validate_builder_name

        _validate_builder_name(name, "MetalLibrary")
        target = Target(
            name,
            target_type=METAL_LIBRARY_TARGET_TYPE,
            defined_at=defined_at or get_caller_location(),
            project=project,
            env=env,
        )
        target._builder_name = "MetalLibrary"

        if sources:
            target.add_sources(sources)
        if depends:
            target.depends(*depends)

        return target


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
