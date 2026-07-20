# SPDX-License-Identifier: MIT
"""Unix toolchain base class for GCC and LLVM.

Provides a shared base class with common functionality for Unix-like
toolchains including:
- Source handler logic for C/C++/Objective-C/assembly files
- Separated argument flags (flags that take arguments as separate tokens)
- Target architecture handling (e.g., -arch on macOS)
- Build variant handling (debug, release, etc.)
- Platform-aware compile flags (e.g., -fPIC for shared libraries)
"""

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

    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.tools.toolchain import SourceHandler

logger = logging.getLogger(__name__)


class UnixToolchain(BaseToolchain):
    """Base class for Unix-like toolchains (GCC, LLVM/Clang).

    This class provides common functionality shared between GCC and LLVM
    toolchains, including source file handling, separated argument flags,
    and variant/architecture application.

    Subclasses should:
    - Call super().__init__(name) in their __init__
    - Override _configure_tools() to configure toolchain-specific tools
    - Override get_source_handler() if they handle additional file types
    """

    # Whether cc/cxx are Clang-family drivers that understand
    # ``--target=<triple>``. GCC (and GCC-based toolchains like gfortran)
    # reject --target=; it's a Clang/clang-cl flag. LlvmToolchain overrides
    # this to True.
    IS_CLANG_DRIVER: ClassVar[bool] = False

    # Named feature presets for common development workflows (see docs/presets.md).
    # Keep them small and orthogonal: `warnings` enables warnings; `werror`
    # promotes them to errors — compose both for the strict combination.
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

    # Flags that take their argument as a separate token (e.g., "-F path" not "-Fpath")
    # These are common GCC/Unix compiler/linker flags where the argument must be
    # a separate element. Both GCC and Clang share these flags.
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
        """Return handler for source file suffix, or None if not handled.

        Handles common C/C++/Objective-C and assembly file types that are
        supported by both GCC and LLVM.

        Args:
            suffix: File suffix including the dot (e.g., ".c", ".cpp").

        Returns:
            SourceHandler if the suffix is handled, None otherwise.
        """
        from pcons.tools.toolchain import SourceHandler

        # Use TargetPath for depfile - resolved to PathToken during resolution
        depfile = TargetPath(suffix=".d")

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", depfile, "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        # Handle case-sensitive .C (C++ on Unix)
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        # Objective-C
        if suffix_lower == ".m":
            return SourceHandler("cc", "objc", ".o", depfile, "gcc")
        if suffix_lower == ".mm":
            return SourceHandler("cxx", "objcxx", ".o", depfile, "gcc")
        # Assembly files - GCC/Clang handles .s (preprocessed) and .S (needs preprocessing)
        # Both are processed by the C compiler which invokes the assembler
        # Check .S (uppercase) first since .S.lower() == ".s"
        if suffix == ".S":
            # .S files need C preprocessing, so they can have dependencies
            return SourceHandler("cc", "asm-cpp", ".o", depfile, "gcc")
        if suffix_lower == ".s":
            # .s files are already preprocessed assembly, no dependency tracking
            return SourceHandler("cc", "asm", ".o", None, None)
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for Unix toolchains."""
        return ".o"

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type.

        For Unix toolchains on Linux, shared libraries need -fPIC.
        On macOS, PIC is the default for 64-bit, so no flag is needed.

        Args:
            target_type: The target type (e.g., "shared_library", "static_library").

        Returns:
            List of additional compile flags.
        """
        platform = get_platform()

        if target_type == "shared_library":
            # On Linux (and other non-macOS POSIX systems), we need -fPIC
            # for position-independent code in shared libraries.
            # On macOS 64-bit, PIC is the default, so no flag needed.
            if platform.is_linux or (platform.is_posix and not platform.is_macos):
                return ["-fPIC"]

        # Static libraries, programs, and other types don't need special flags
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
        """Return flags that take their argument as a separate token.

        Returns:
            A frozenset of GCC/Unix flags that take separate arguments.
        """
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
        """On macOS, add -arch for universal builds; elsewhere no flags.

        On Linux, cross-compilation uses a different toolchain (or a triple via
        a cross-preset), so a bare arch adds nothing here.
        """
        if get_platform().is_macos:
            return [
                ToolContribution(t, flags=("-arch", arch))
                for t in ("cc", "cxx", "link")
            ]
        return []

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
        return [
            ToolContribution("cc", flags=tuple(flags), defines=tuple(defines)),
            ToolContribution("cxx", flags=tuple(flags), defines=tuple(defines)),
        ]
