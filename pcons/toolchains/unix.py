# SPDX-License-Identifier: MIT
"""Unix toolchain base class for GCC and LLVM: shared source handling,
separated-argument flags, arch/variant handling, and platform-aware
compile flags."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from pcons.configure.platform import get_platform
from pcons.core.preset import ToolContribution
from pcons.core.subst import TargetPath
from pcons.tools.toolchain import BaseToolchain
from pcons.util.macos import apple_sdk_for_triple

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment
    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)


class UnixToolchain(BaseToolchain):
    """Base class for Unix-like toolchains (GCC, LLVM/Clang).

    Subclasses override _configure_tools(), and get_source_handler() if
    they handle additional file types.
    """

    # True when this toolchain's declared cc/cxx tools are Clang-family
    # drivers that accept ``--target=<triple>``. GCC (and GCC-based
    # toolchains like gfortran) reject --target= and select targets by
    # binary instead; LlvmToolchain overrides this to True. This describes
    # the declared cc/cxx tools only — a toolchain with no cc/cxx at all
    # (e.g. Swift) leaves it False, meaning "nothing to retarget", not
    # "my compiler isn't clang".
    IS_CLANG_DRIVER: ClassVar[bool] = False

    # True for toolchains whose output is WebAssembly (see WasmToolchain).
    # wasm cross presets applied to a non-wasm toolchain fail fast: they
    # could repoint the compilers but not the output suffixes, shared-lib
    # rules, or link driver, which live in the dedicated toolchains.
    TARGETS_WASM: ClassVar[bool] = False

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross preset, rejecting wasm targets on native toolchains.

        A wasm preset on a native toolchain would half-apply (compile with
        emcc, link with the host driver) — output suffixes, shared-library
        rules, and the link driver live in the dedicated wasm toolchains.
        """
        triple = str(getattr(preset, "triple", None) or "")
        if triple.startswith("wasm32") and not self.TARGETS_WASM:
            name = getattr(preset, "name", preset)
            raise ValueError(
                f"Cross preset '{name}' targets WebAssembly, which needs its "
                f"dedicated toolchain rather than {self.name}: use "
                f'project.Environment(toolchain="emscripten") or '
                f'toolchain="wasi".'
            )
        super().apply_cross_preset(env, preset)

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
            # Xlinker passthrough
            "-Xlinker",
            "-Xpreprocessor",
            "-Xassembler",
        ]
    )

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

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type.

        Shared libraries need -fPIC on Linux and other non-macOS POSIX
        systems; on 64-bit macOS PIC is the default.
        """
        platform = get_platform()

        if target_type == "shared_library":
            if platform.is_linux or (platform.is_posix and not platform.is_macos):
                return ["-fPIC"]

        return []

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[str | PathToken],
    ) -> list[str]:
        """Return install_name (macOS) or SONAME (Linux) for shared libraries.

        Uses ``target.install_name`` if set:
        - ``None``: auto-generate from *output_name*
        - a string: use as-is
        - ``""``: disable entirely
        """
        if target.target_type != "shared_library":
            return []

        # Check for explicit user setting via target.set("install_name", ...)
        explicit = (
            target.get_option("install_name") if hasattr(target, "get_option") else None
        )
        if explicit == "":
            return []  # explicitly disabled

        platform = get_platform()

        if platform.is_macos:
            name = explicit if explicit is not None else f"@rpath/{output_name}"
            return [f"-Wl,-install_name,{name}"]
        elif platform.is_linux:
            name = explicit if explicit is not None else output_name
            return [f"-Wl,-soname,{name}"]

        return []

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
                for t in ("cc", "cxx", "link")
            ]
        raise ValueError(
            f"{self.name} cannot retarget the CPU to '{arch}' by flag on "
            f"this platform. Use a cross preset (e.g. "
            f"linux_cross(triple=...)) or a cross toolchain instead; see "
            f"docs/presets.md."
        )

    def _target_contributions(self, cross: Any) -> list[ToolContribution]:
        """Base contributions plus --target triple (Clang only) and --sysroot.

        GCC uses different toolchain binaries rather than a --target flag, and
        rejects --target= outright, so it's only emitted for Clang-family
        drivers (see IS_CLANG_DRIVER). Clang also drives the link, so the
        triple goes on the link command too. For Apple triples with no
        explicit sysroot, the matching SDK is resolved via xcrun (mirroring
        the Swift toolchain), so ios() works out of the box for C/C++.
        """
        contribs = super()._target_contributions(cross)
        contribs.extend(self._sysroot_contributions(cross))
        triple = getattr(cross, "triple", None)
        if triple and self.IS_CLANG_DRIVER:
            target_flag = f"--target={triple}"
            for tool in ("cc", "cxx", "link"):
                contribs.append(ToolContribution(tool, flags=(target_flag,)))
            if not getattr(cross, "sysroot", None):
                sdk = apple_sdk_for_triple(str(triple))
                if sdk:
                    for tool in ("cc", "cxx", "link"):
                        contribs.append(
                            ToolContribution(tool, flags=("-isysroot", sdk))
                        )
        return contribs

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        spec = self.UNIX_VARIANTS.get(variant.lower())
        if spec is None:
            raise ValueError(
                f"Unknown variant '{variant}'. "
                f"Supported variants: debug, release, relwithdebinfo, minsizerel."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        # Realized on the same compile tools as feature presets, so
        # Fortran-style toolchains (fc) get working variants too.
        return [
            ToolContribution(tool, flags=tuple(flags), defines=tuple(defines))
            for tool in self._feature_preset_tools()
        ]
