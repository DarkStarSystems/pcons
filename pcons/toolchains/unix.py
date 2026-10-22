# SPDX-License-Identifier: MIT
"""Unix toolchain base class for GCC and LLVM: shared source handling,
separated-argument flags, arch/variant handling, and platform-aware
compile flags."""

from __future__ import annotations

import functools
import itertools
import logging
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from pcons.configure.platform import Platform, get_platform
from pcons.core.errors import PconsValueError
from pcons.core.preset import Preset, ToolContribution
from pcons.core.subst import PathToken, TargetPath
from pcons.core.target import register_target_option
from pcons.toolchains._msvc_compat import module_definition, msvc_crt
from pcons.toolchains.gnu_common import (
    GNU_COMPILE_PATH_FLAGS,
    GNU_LINK_PATH_FLAGS,
    needs_pic,
)
from pcons.tools.toolchain import BaseToolchain, LinkNeeds
from pcons.util.macos import apple_sdk_for_triple

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.subst import FlagToken
    from pcons.core.target import Target
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)

register_target_option(
    "install_name",
    'shared-library install name (macOS) or SONAME (Linux); "" disables the '
    "automatic default",
)

#: Systems whose dynamic loader reads $ORIGIN in an ELF rpath.
_ORIGIN_SYSTEMS = frozenset({"linux", "android", "freebsd", "openbsd", "netbsd"})


def abi_from_triple(triple: str) -> str | None:
    """The Windows ABI a target triple names: "msvc" for ``*-msvc``,
    "mingw" for ``*-gnu`` and ``*-mingw*``; None when it names neither."""
    if triple.endswith("-msvc"):
        return "msvc"
    if triple.endswith("-gnu") or "mingw" in triple:
        return "mingw"
    return None


def _sanitizes(flags: Sequence[FlagToken], sanitizer: str) -> bool:
    """Whether *flags* already enable *sanitizer* (``-fsanitize=a,b``)."""
    return any(
        isinstance(f, str)
        and f.startswith("-fsanitize=")
        and sanitizer in f.removeprefix("-fsanitize=").split(",")
        for f in flags
    )


def static_archive(lib: str, platform: Platform) -> str:
    """The static archive of library *lib*, in GNU ld's explicit file name
    form (``:libstdc++.a``), for a link whose driver doesn't link that
    runtime itself. Apple's linker has no such form."""
    if platform.is_apple:
        raise PconsValueError(
            f"On Apple's platforms, only its own language's driver can link "
            f"lib{lib} static: set the target's link_language to that language."
        )
    return f":lib{lib}.a"


@functools.cache
def is_apple_clang(exe: str) -> bool:
    """Whether *exe* is Apple's clang, by its ``--version`` (cached)."""
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(out) and "Apple" in out.splitlines()[0]


@functools.cache
def target_triple(compiler: str) -> str | None:
    """*compiler*'s default target triple, from ``-print-target-triple``
    (clang and flang; GCC has ``-dumpmachine`` instead), or None when it
    can't be asked."""
    try:
        out = subprocess.check_output(
            [compiler, "-print-target-triple"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip() or None


@functools.cache
def _driver_rpaths(driver: str) -> frozenset[str]:
    """The rpaths *driver* passes the linker by itself, from what ``-###``
    says it would run (cached). Nothing runs: the input needn't exist."""
    try:
        out = subprocess.run(
            [driver, "-###", "-o", "a.out", "probe.o"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stderr
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    rpaths: set[str] = set()
    for line in out.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        rpaths.update(
            path for flag, path in itertools.pairwise(tokens) if flag == "-rpath"
        )
    return frozenset(rpaths)


#: Driver flags that hand the token after them to a sub-tool. Named here so
#: the two flag sets below can share one list rather than repeat it.
_PASSTHROUGH_FLAGS: frozenset[str] = frozenset(
    ["-Xlinker", "-Xpreprocessor", "-Xassembler", "-Xclang"]
)


def _chooses_subsystem(flags: Sequence[FlagToken]) -> bool:
    """Whether *flags* already name a Windows subsystem."""
    return any(isinstance(f, str) and "/SUBSYSTEM:" in f.upper() for f in flags)


class UnixToolchain(BaseToolchain):
    """Base class for Unix-like toolchains (GCC, LLVM/Clang).

    Subclasses override get_source_handler() if they handle additional
    file types.
    """

    # True when this toolchain's compile tools (_feature_preset_tools) are
    # Clang-family drivers that accept ``--target=<triple>``. GCC (and
    # GCC-based toolchains like gfortran) reject --target= and select
    # targets by binary instead; LlvmToolchain and FlangToolchain override
    # this to True. Swift leaves it False: swiftc takes ``-target``, which
    # SwiftToolchain realizes itself.
    IS_CLANG_DRIVER: ClassVar[bool] = False

    # The ABI this toolchain's objects follow when it runs on Windows, where
    # MinGW ("mingw") and MSVC ("msvc") objects can't be linked together.
    # Elsewhere every Unix toolchain links with every other: no tag.
    WINDOWS_ABI: ClassVar[str | None] = None

    @property
    def abi(self) -> str | None:
        """On Windows, the compiler's target triple's ABI when it says,
        else WINDOWS_ABI. A clang-driver toolchain may target either: LLVM's
        own builds target MSVC, MSYS2's clang64 ones MinGW."""
        if not get_platform().is_windows:
            return None
        compiler = self._abi_probe_compiler()
        triple = target_triple(compiler) if compiler else None
        return (abi_from_triple(triple) if triple else None) or self.WINDOWS_ABI

    def _abi_probe_compiler(self) -> str | None:
        """The compiler whose target triple decides this toolchain's ABI,
        or None when WINDOWS_ABI always holds."""
        return None

    # True for toolchains whose output is WebAssembly (see WasmToolchain).
    # wasm cross presets applied to a non-wasm toolchain fail fast: they
    # could repoint the compilers but not the output suffixes, shared-lib
    # rules, or link driver, which live in the dedicated toolchains.
    TARGETS_WASM: ClassVar[bool] = False

    def make_target_preset(self, cross: Any) -> Preset:
        """A cross preset, rejecting wasm targets on native toolchains.

        A wasm preset on a native toolchain would half-apply (compile with
        emcc, link with the host driver) — output suffixes, shared-library
        rules, and the link driver live in the dedicated wasm toolchains.
        """
        triple = str(getattr(cross, "triple", None) or "")
        if triple.startswith("wasm32") and not self.TARGETS_WASM:
            name = getattr(cross, "name", cross)
            raise PconsValueError(
                f"Cross preset '{name}' targets WebAssembly, which needs its "
                f"dedicated toolchain rather than {self.name}: use "
                f'project.Environment(toolchain="emscripten") or '
                f'toolchain="wasi".'
            )
        return super().make_target_preset(cross)

    # Named feature presets; see docs/presets.md.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {
            "compile_flags": ["-Wall", "-Wextra", "-Wpedantic"],
        },
        "werror": {
            "compile_flags": ["-Werror"],
        },
        "sanitize": {
            "compile_flags": [
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
            ],
            "link_flags": ["-fsanitize=address,undefined"],
        },
        "profile": {
            "compile_flags": ["-pg", "-g"],
            "link_flags": ["-pg"],
        },
        "lto": {
            "compile_flags": ["-flto"],
            "link_flags": ["-flto"],
        },
        "hardened": {
            "compile_flags": [
                "-fstack-protector-strong",
                "-D_FORTIFY_SOURCE=2",
                "-fPIE",
            ],
            "link_flags": ["-pie", "-Wl,-z,relro,-z,now"],
        },
        "pthread": {
            "compile_flags": ["-pthread"],
            "link_flags": ["-pthread"],
        },
        "coverage": {
            "compile_flags": ["--coverage"],
            "link_flags": ["--coverage"],
        },
        "fast-math": {
            "compile_flags": ["-ffast-math"],
            "link_flags": ["-ffast-math"],
        },
        # Realized dynamically in make_feature_preset(): Apple clang has no
        # bundled OpenMP runtime, so realization needs detection. The empty
        # entry declares the name (error messages, KnownFeaturePreset).
        "openmp": {},
    }

    # Flags that take their argument as a separate token ("-F path", not
    # "-Fpath"). Shared by GCC and Clang.
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # Framework/library paths (macOS)
            "-F",
            "-framework",
            # Xcode/Apple toolchain
            "-iframework",
            # Linker flags that take arguments
            "-Wl,-rpath",
            "-Wl,-install_name",
            "-Wl,-soname",
            # Output-related
            "-o",
            "-MF",
            "-MT",
            "-MQ",
            # Linker script
            "-T",
            # Architecture
            "-arch",
            "-target",
            "--target",
            # Include/library search modifiers
            "-isystem",
            "-isysroot",
            "-iquote",
            "-idirafter",
            # Force-include headers
            "-include",
            "-imacros",
            # Language specification
            "-x",
            # Driver pass-through (also PASSTHROUGH_FLAGS below)
            *_PASSTHROUGH_FLAGS,
        ]
    )

    #: Flags that hand their argument to a sub-tool. Consecutive ones form
    #: a single directive -- ``-Xlinker -rpath -Xlinker /p`` is ``-rpath
    #: /p`` to the linker -- so they are never dropped as duplicates:
    #: dropping a repeated ``-Xlinker -rpath`` would leave the next path
    #: with no directive in front of it.
    PASSTHROUGH_FLAGS: frozenset[str] = _PASSTHROUGH_FLAGS

    def get_passthrough_flags(self) -> frozenset[str]:
        """Flags whose argument is passed through to a sub-tool verbatim."""
        return self.PASSTHROUGH_FLAGS

    # =========================================================================
    # Feature presets needing detection (see docs/presets.md)
    # =========================================================================

    # Homebrew libomp locations (arm64, then Intel) tried for Apple clang.
    LIBOMP_PREFIXES: ClassVar[tuple[str, ...]] = (
        "/opt/homebrew/opt/libomp",
        "/usr/local/opt/libomp",
    )

    def make_feature_preset(
        self, name: str, target: Platform | None = None
    ) -> Preset | None:
        """Realize feature presets, resolving ``openmp`` dynamically where
        its entry is empty (a toolchain with its own flags lists them)."""
        if name == "openmp" and self.FEATURE_PRESETS.get(name) == {}:
            # Each tool by the compiler it runs: $CC can name Homebrew's
            # clang while $CXX stays Apple's. A program with C++ in it links
            # with the C++ driver, so the link goes by that compiler.
            contribs = []
            for tool in (*self._feature_preset_tools(), "link"):
                runs = "cxx" if tool == "link" and "cxx" in self._tools else tool
                flags = self._openmp_flags(self._compiler_is_apple_clang(runs))
                if flags is None:
                    return None
                compile_flags, link_flags = flags
                contribs.append(
                    ToolContribution(
                        tool,
                        flags=tuple(link_flags if tool == "link" else compile_flags),
                    )
                )
            return Preset(
                name="openmp", category="feature", contributions=tuple(contribs)
            )
        return super().make_feature_preset(name, target)

    def _feature_spec(self, name: str, target: Platform) -> dict[str, list[str]] | None:
        """``hardened`` is written for ELF, so it's adjusted for Mach-O and
        PE, whose linkers reject ``-z`` and whose programs are
        position-independent already. For the MSVC ABI, stack protection is
        MSVC's /GS and Control Flow Guard stands in for RELRO, as in MSVC's
        own preset; MinGW's linker marks programs for ASLR and DEP."""
        spec = super()._feature_spec(name, target)
        if name != "hardened" or spec is None:
            return spec
        if not (target.is_apple or target.is_windows):
            return spec
        compile_flags = [f for f in spec.get("compile_flags", []) if f != "-fPIE"]
        if target.is_apple:
            return {"compile_flags": compile_flags}
        if self._targets_msvc_abi(target):
            compile_flags = [
                f for f in compile_flags if not f.startswith("-D_FORTIFY_SOURCE")
            ]
            return {
                "compile_flags": [*compile_flags, "-Xclang", "-cfguard"],
                "link_flags": ["-Wl,/guard:cf"],
            }
        return {
            "compile_flags": compile_flags,
            "link_flags": ["-Wl,--dynamicbase,--nxcompat"],
        }

    def _openmp_flags(self, apple_clang: bool) -> tuple[list[str], list[str]] | None:
        """(compile_flags, link_flags) enabling OpenMP for a compiler that
        is or isn't Apple clang, or None if unavailable.

        GCC and open-source clang bundle an OpenMP runtime, so ``-fopenmp``
        is all that's needed. Apple clang ships the pragma support but no
        runtime; there we look for Homebrew's libomp and pass it explicitly.
        Note this answers "can the compiler enable OpenMP", flag-level only —
        it cannot prove every OpenMP construct compiles.
        """
        if not apple_clang:
            return (["-fopenmp"], ["-fopenmp"])
        from pathlib import Path

        for prefix in self.LIBOMP_PREFIXES:
            if (Path(prefix) / "include" / "omp.h").is_file():
                return (
                    ["-Xclang", "-fopenmp", f"-I{prefix}/include"],
                    [f"-L{prefix}/lib", "-lomp"],
                )
        return None

    def _compiler_is_apple_clang(self, tool_name: str = "cxx") -> bool:
        """Whether the compiler the *tool_name* tool runs (_tool_command) is
        Apple clang: on macOS ``gcc``/``g++`` are usually Apple clang
        shims, so the toolchain name alone can't answer this."""
        import sys

        if sys.platform != "darwin" or tool_name not in self._tools:
            return False
        exe = self._tool_command(tool_name)
        return exe is not None and is_apple_clang(exe)

    # =========================================================================
    # Source Handler Methods
    # =========================================================================

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for C/C++/Objective-C/assembly suffixes, or None."""
        from pcons.tools.toolchain import SourceHandler

        depfile = TargetPath(suffix=".d")

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", depfile, "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        # Case-sensitive .C is C++ on Unix
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        # Objective-C
        if suffix_lower == ".m":
            return SourceHandler("cc", "objc", ".o", depfile, "gcc")
        if suffix_lower == ".mm":
            return SourceHandler("cxx", "objcxx", ".o", depfile, "gcc")
        # Assembly goes through the C compiler driver. Check .S (uppercase)
        # first since .S.lower() == ".s": .S needs C preprocessing (so it can
        # have dependencies); .s is already preprocessed.
        if suffix == ".S":
            return SourceHandler("cc", "asm-cpp", ".o", depfile, "gcc")
        if suffix_lower == ".s":
            return SourceHandler("cc", "asm", ".o", None, None)
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for Unix toolchains."""
        return ".o"

    def get_compile_flags_for_target_type(
        self,
        target_type: str,
        env: Environment | None = None,
        *,
        pic: bool | None = None,
    ) -> list[str]:
        """Return additional compile flags needed for the target type.

        ``-fPIC`` where :func:`needs_pic` says, for the platform *env*
        builds for, when it says.
        """
        platform = env.target if env is not None else get_platform()
        return ["-fPIC"] if needs_pic(target_type, platform, pic) else []

    @staticmethod
    def _target_platform(target: Target) -> Platform:
        """The platform *target* is built for: its environment's, or the
        host's when it has none."""
        env = getattr(target, "_env", None)
        return env.target if env is not None else get_platform()

    #: Flag that carries a shared library's own name, per platform.
    _INSTALL_NAME_FLAGS = {"macos": "-Wl,-install_name,", "linux": "-Wl,-soname,"}

    @staticmethod
    def _names_itself_already(
        flag: str, existing_flags: Sequence[FlagToken], env: Environment | None
    ) -> bool:
        """Whether the caller has already set this library's own name.

        Every spelling the linker accepts: ``-Wl,-soname,x``, ``-Wl,-soname=x``
        and the ``-Xlinker -soname -Xlinker x`` long form. ``env.link.flags``
        is searched as well as the usage requirements, since a project may set
        it in either and the environment's are merged in after this runs.
        """
        stem = flag.rstrip(",")  # "-Wl,-soname"
        bare = stem.split(",")[-1]  # "-soname"
        candidates = list(existing_flags)
        if env is not None and env.has_tool("link"):
            candidates += list(getattr(env.link, "flags", None) or [])
        strings = [f for f in candidates if isinstance(f, str)]
        return any(
            f.startswith(f"{stem},") or f.startswith(f"{stem}=") or f == bare
            for f in strings
        )

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[FlagToken],
    ) -> list[FlagToken]:
        """Return install_name (macOS) or SONAME (Linux) for shared libraries.

        Reads ``target.get_option("install_name")``:
        - ``None``: name the library after its own output file
        - a string: use as-is
        - ``""``: disable entirely

        The automatic name is a ``TargetPath(basename=True)`` marker rather
        than a formatted string, so the flag reads the same in every target's
        rule and they share one. Writing the filename in would give each
        shared library a private copy of the whole link rule.
        """
        if target.target_type not in ("shared_library", "program"):
            return []
        flags = self._exported_symbols_flags(target)
        if target.target_type == "shared_library":
            flags = [*self._install_name_flags(target, existing_flags), *flags]
            if self._writes_import_library(self._target_platform(target)):
                # Where get_import_library_name says (the link's second output)
                flags.append(TargetPath(prefix="-Wl,/IMPLIB:", index=1))
        return flags

    def get_import_library_name(
        self, shared_library_name: str, target: Platform | None = None
    ) -> str | None:
        """``foo.lib`` for a DLL linked for the MSVC ABI, as by clang or
        flang targeting it: their driver hands the link to link.exe or
        lld-link, which writes the import library a consumer links against.
        MinGW's ld links a DLL directly and writes none."""
        if not self._writes_import_library(target or get_platform()):
            return None
        return str(PurePosixPath(shared_library_name).with_suffix(".lib"))

    def _writes_import_library(self, platform: Platform) -> bool:
        """Whether a DLL link for *platform* is an MSVC-ABI one."""
        return self._targets_msvc_abi(platform)

    def _targets_msvc_abi(self, platform: Platform) -> bool:
        """Whether this toolchain builds for the MSVC ABI on *platform*. A
        cross target says so by its naming (an MSVC triple's archives are
        .lib, a MinGW one's .a); the host, by what this toolchain's compiler
        targets."""
        if not platform.is_windows:
            return False
        if platform != get_platform():
            return platform.static_lib_suffix == ".lib"
        return self.abi == "msvc"

    def link_group_tokens(
        self, archives: Sequence[PathToken], env: Environment | None = None
    ) -> list[FlagToken] | None:
        """GNU ld and lld resolve a cycle of archives only inside a group;
        Apple's ld rescans on its own and takes no group option. Which
        linker is *env*'s target's, not the host's."""
        platform = env.target if env is not None else get_platform()
        if platform.is_apple:
            return None
        return ["-Wl,--start-group", *archives, "-Wl,--end-group"]

    def _exported_symbols_flags(self, target: Target) -> list[FlagToken]:
        """The linker's own way to export only the named symbols.

        For a shared library or an executable that plugins call back into.
        macOS takes a symbol list (``-exported_symbols_list``) for either;
        GNU ld a version script for a library and a dynamic list for an
        executable. All accept ``*`` patterns. An MSVC-ABI link (clang or
        flang targeting it) takes a ``.def`` file, as MSVC's does, and
        exact names only. Names are the C names: the Darwin underscore is
        added here. The file is written in the target's object directory
        and, being registered, the link depends on it.
        """
        symbols = (
            target.get_option("exported_symbols")
            if hasattr(target, "get_option")
            else None
        )
        if not symbols:
            return []
        platform = self._target_platform(target)
        if platform.is_apple:
            text = "".join(
                f"{s}\n" if s.startswith("_") else f"_{s}\n" for s in symbols
            )
            return [
                self._link_input_token(
                    target, ".exports", text, "-Wl,-exported_symbols_list,"
                )
            ]
        if not platform.is_windows:
            if target.target_type == "program":
                # A version script only restricts what an executable would
                # export, which without --export-dynamic is nothing; a
                # dynamic list names what it exports, and only that.
                text = "{ " + " ".join(f"{s};" for s in symbols) + " };\n"
                return [
                    self._link_input_token(
                        target, ".dynlist", text, "-Wl,--dynamic-list="
                    )
                ]
            text = "{ global: " + " ".join(f"{s};" for s in symbols) + " local: *; };\n"
            return [
                self._link_input_token(
                    target, ".version", text, "-Wl,--version-script="
                )
            ]
        if self._writes_import_library(platform):
            text = module_definition(target, symbols)
            return [self._link_input_token(target, ".def", text, "-Wl,/DEF:")]
        logger.warning(
            "%s: exported_symbols is not realized for %s targets with %s; "
            "every symbol stays exported",
            target.name,
            platform.os,
            self.name,
        )
        return []

    def _install_name_flags(
        self, target: Target, existing_flags: Sequence[FlagToken]
    ) -> list[FlagToken]:
        explicit = (
            target.get_option("install_name") if hasattr(target, "get_option") else None
        )
        if explicit == "":
            return []  # explicitly disabled

        platform = self._target_platform(target)
        if platform.is_apple:
            flag, auto_prefix = self._INSTALL_NAME_FLAGS["macos"], "@rpath/"
        elif platform.is_windows:
            return []
        else:
            flag, auto_prefix = self._INSTALL_NAME_FLAGS["linux"], ""

        # A hand-written one wins. The marker can't be compared against the
        # caller's string flags, so this is what `existing_flags` is for, and
        # the automatic flag is appended after theirs — ld takes the last one,
        # so missing theirs would override it rather than duplicate it.
        if self._names_itself_already(
            flag, existing_flags, getattr(target, "_env", None)
        ):
            return []

        if explicit is not None:
            # Not derived from the output, so it stays literal — and this
            # target gets a rule of its own, which is what asking for a
            # specific name means.
            return [f"{flag}{explicit}"]
        return [TargetPath(basename=True, prefix=f"{flag}{auto_prefix}")]

    COMPILE_PATH_FLAGS: ClassVar[frozenset[str]] = GNU_COMPILE_PATH_FLAGS
    LINK_PATH_FLAGS: ClassVar[frozenset[str]] = GNU_LINK_PATH_FLAGS

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token."""
        return self.SEPARATED_ARG_FLAGS

    # =========================================================================
    # Target Architecture and Variant Methods
    # =========================================================================

    # Variant flags per build type (compile_flags, defines).
    UNIX_VARIANTS: dict[str, tuple[list[str], list[str]]] = {
        "debug": (["-O0", "-g"], ["DEBUG", "_DEBUG"]),
        "release": (["-O2"], ["NDEBUG"]),
        # The compiler's highest safe optimization level. Nothing that
        # changes results, so no fast-math: a script that wants it says so.
        "release-fastest": (["-O3"], ["NDEBUG"]),
        "relwithdebinfo": (["-O2", "-g"], ["NDEBUG"]),
        "minsizerel": (["-Os"], ["NDEBUG"]),
    }

    def _cxx_standard_flag(self, standard: int) -> str:
        return f"-std=c++{standard}"

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """On macOS, add -arch for universal builds; elsewhere unrealizable.

        Off macOS a bare arch name cannot retarget GCC/Clang — that needs a
        triple (cross preset) or different tool binaries — so this raises
        rather than silently building for the host CPU.
        """
        if get_platform().is_macos:
            return [
                ToolContribution(t, flags=("-arch", arch))
                for t in (*self._feature_preset_tools(), "link")
            ]
        raise PconsValueError(
            f"{self.name} cannot retarget the CPU to '{arch}' by flag on "
            f"this platform. Use a cross preset (e.g. "
            f"linux_cross(triple=...)) or a cross toolchain instead; see "
            f"docs/presets.md."
        )

    def _target_contributions(self, cross: Any) -> list[ToolContribution]:
        """Base contributions plus --target triple (Clang only) and --sysroot.

        GCC uses different toolchain binaries rather than a --target flag, and
        rejects --target= outright, so it's only emitted for Clang-family
        drivers (see IS_CLANG_DRIVER). It goes on the toolchain's own
        compile tools (cc and cxx for clang, fc for flang) and, since the
        driver also links, on the link command too. For Apple triples with no
        explicit sysroot, the matching SDK is resolved via xcrun (mirroring
        the Swift toolchain), so ios() works out of the box for C/C++.
        """
        contribs = super()._target_contributions(cross)
        contribs.extend(self._sysroot_contributions(cross))
        triple = getattr(cross, "triple", None)
        if triple and self.IS_CLANG_DRIVER:
            tools = (*self._feature_preset_tools(), "link")
            target_flag = f"--target={triple}"
            for tool in tools:
                contribs.append(ToolContribution(tool, flags=(target_flag,)))
            if not getattr(cross, "sysroot", None):
                sdk = apple_sdk_for_triple(str(triple))
                if sdk:
                    for tool in tools:
                        contribs.append(
                            ToolContribution(tool, flags=("-isysroot", sdk))
                        )
        return contribs

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """C++ objects linked by another language's driver (gfortran's,
        swiftc's) need the C++ runtime that g++ or clang++ would add.
        MSVC-ABI objects name theirs."""
        if self.abi == "msvc" or not languages & {"cxx", "objcxx"}:
            return LinkNeeds()
        if link_language in ("cxx", "objcxx", "cuda"):
            return LinkNeeds()
        platform = self._target_platform(target)
        lib = "c++" if platform.is_apple else "stdc++"
        if env.runtime_linkage == "static":
            return LinkNeeds(libs=(static_archive(lib, platform),))
        return LinkNeeds(libs=(lib,))

    def realize_link_needs(
        self,
        needs: LinkNeeds,
        target: Target,
        existing_flags: Sequence[FlagToken],
        driver: str | None = None,
    ) -> list[FlagToken]:
        """rpaths as ``-Wl,-rpath,``, a relative one from ``@loader_path``
        (Mach-O) or ``$ORIGIN`` (ELF), unless the script or the *driver*
        gives it already; objects as they are; excluded default libraries and
        a console entry point only for the MSVC ABI, where the driver runs
        link.exe or lld-link."""
        platform = self._target_platform(target)
        flags: list[FlagToken] = list(needs.objects)
        # The driver links each sanitizer's runtime as it would for its own
        # objects.
        flags += [
            f"-fsanitize={s}"
            for s in needs.sanitizers
            if not _sanitizes(existing_flags, s)
        ]
        # ld64 warns about a repeated rpath, and GCC's driver passes its own
        # (@loader_path and its library directories) on Apple platforms.
        given = _driver_rpaths(driver) if driver and platform.is_apple else frozenset()
        if not platform.is_windows:
            flags += [f"-Wl,-rpath,{d}" for d in needs.rpaths if d not in given]
        origin = (
            "@loader_path"
            if platform.is_apple
            else "$$ORIGIN"
            if platform.os in _ORIGIN_SYSTEMS
            else None
        )
        if origin is not None:
            for d in needs.relative_rpaths:
                path = origin if d == "." else f"{origin}/{d}"
                rpath = f"-Wl,-rpath,{path}"
                if (
                    path not in given
                    and rpath not in existing_flags
                    and rpath not in flags
                ):
                    flags.append(rpath)
        if self._targets_msvc_abi(platform):
            if needs.console and not _chooses_subsystem(existing_flags):
                flags.append("-Wl,/SUBSYSTEM:CONSOLE")
            flags += [f"-Wl,/NODEFAULTLIB:{lib}" for lib in needs.excluded_default_libs]
        return flags

    #: The compile tool whose driver links each language: C++ objects need
    #: the C++ driver for its runtime, and CUDA links through it too.
    LINK_DRIVER_TOOLS: ClassVar[dict[str, str]] = {
        "c": "cc",
        "objc": "cc",
        "cxx": "cxx",
        "objcxx": "cxx",
        "cuda": "cxx",
    }

    def link_driver(self, language: str, env: Environment) -> str | None:
        """The driver of this toolchain's own compile tool for *language*.
        Targeting MSVC, only for its own link tool (clang++ for C++, as
        always): its objects name their runtimes, so another toolchain's
        link takes them as they are."""
        tool = self.LINK_DRIVER_TOOLS.get(language)
        if tool is None or not env.has_tool(tool):
            return None
        if self.abi == "msvc" and env.toolchain_for("link") is not self:
            return None
        if env.toolchain_for(tool) is not self:
            return None
        cmd = getattr(env, tool).cmd
        return str(cmd) if cmd else None

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        spec = self.UNIX_VARIANTS.get(variant.lower())
        if spec is None:
            raise PconsValueError(
                f"Unknown variant '{variant}'. "
                f"Supported variants: debug, release, release-fastest, "
                f"relwithdebinfo, minsizerel."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        # Realized on the same compile tools as feature presets, so
        # Fortran-style toolchains (fc) get working variants too.
        contribs = [
            ToolContribution(tool, flags=tuple(flags), defines=tuple(defines))
            for tool in self._feature_preset_tools()
        ]
        # Targeting MSVC, the driver writes a PDB only when it links with -g.
        if "-g" in flags and self.abi == "msvc":
            contribs.append(ToolContribution("link", flags=("-g",)))
        return contribs

    # What links this toolchain's runtimes statically, on its link driver:
    # -static-libgcc -static-libstdc++ for GCC's. None if it can't.
    STATIC_RUNTIME_LINK_FLAGS: ClassVar[tuple[str, ...] | None] = None

    def _runtime_contributions(
        self, linkage: str, variant: str, target: Platform
    ) -> list[ToolContribution] | None:
        """Targeting MSVC, the C runtime msvc_crt chooses, on each compile
        tool (``-fms-runtime-lib``). Otherwise the drivers link the shared
        runtimes by default, and STATIC_RUNTIME_LINK_FLAGS the static ones.
        clang on Apple's platforms links the system's libc++, which is
        shared only."""
        if self._targets_msvc_abi(target):
            flag = f"-fms-runtime-lib={msvc_crt(linkage, variant)}"
            return [
                ToolContribution(tool, flags=(flag,))
                for tool in self._feature_preset_tools()
            ]
        if linkage == "shared":
            return []
        if target.is_apple and (
            self.IS_CLANG_DRIVER or self._compiler_is_apple_clang()
        ):
            raise PconsValueError(
                f"{self.name} can't link its runtime static: on Apple's "
                f"platforms, clang links the system's C++ runtime, which is "
                f"shared only."
            )
        if self.STATIC_RUNTIME_LINK_FLAGS is None:
            return None
        return [ToolContribution("link", flags=self.STATIC_RUNTIME_LINK_FLAGS)]
