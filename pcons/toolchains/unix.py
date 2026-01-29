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
from typing import TYPE_CHECKING, Any

from pcons.configure.platform import get_platform
from pcons.core.subst import TargetPath
from pcons.tools.toolchain import BaseToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment
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

    # Named flag presets for common development workflows.
    # Keys are preset names; values map tool categories to flag lists.
    UNIX_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {
            "compile_flags": ["-Wall", "-Wextra", "-Wpedantic", "-Werror"],
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

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library (Unix-style)."""
        return f"lib{name}.a"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library (platform-aware)."""
        platform = get_platform()
        if platform.is_windows:
            # GCC/MinGW on Windows produces .dll files
            return f"{name}.dll"
        if platform.is_macos:
            return f"lib{name}.dylib"
        return f"lib{name}.so"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program (platform-aware)."""
        platform = get_platform()
        if platform.is_windows:
            # GCC/MinGW on Windows produces .exe files
            return f"{name}.exe"
        return name

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

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        Returns:
            A frozenset of GCC/Unix flags that take separate arguments.
        """
        return self.SEPARATED_ARG_FLAGS

    # =========================================================================
    # Target Architecture and Variant Methods
    # =========================================================================

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags.

        On macOS, uses the -arch flag for cross-compilation (e.g., building
        arm64 binaries on x86_64 or vice versa). This enables building
        universal binaries by compiling each architecture separately and
        combining with lipo.

        On Linux, cross-compilation typically requires a different toolchain,
        so this method is a no-op there (use a different toolchain instead).

        Args:
            env: Environment to modify.
            arch: Architecture name (e.g., "arm64", "x86_64").
            **kwargs: Toolchain-specific options (unused).
        """
        super().apply_target_arch(env, arch, **kwargs)
        platform = get_platform()

        if platform.is_macos:
            # macOS uses -arch flag for universal binary builds
            arch_flags = ["-arch", arch]
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.extend(arch_flags)
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.extend(arch_flags)

    def apply_preset(self, env: Environment, name: str) -> None:
        """Apply a named flag preset.

        Args:
            env: Environment to modify.
            name: Preset name (warnings, sanitize, profile, lto, hardened).
        """
        preset = self.UNIX_PRESETS.get(name)
        if preset is None:
            logger.warning("Unknown preset '%s' for Unix toolchain", name)
            return

        compile_flags = preset.get("compile_flags", [])
        link_flags = preset.get("link_flags", [])

        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)

        if env.has_tool("link") and link_flags:
            if isinstance(env.link.flags, list):
                env.link.flags.extend(link_flags)

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset.

        Handles --target triple (Clang only) and --sysroot flags,
        then delegates to BaseToolchain for generic fields.

        Args:
            env: Environment to modify.
            preset: A CrossPreset dataclass instance.
        """
        # Apply target triple (Clang/LLVM only — GCC uses different
        # toolchain binaries rather than --target flag)
        if hasattr(preset, "triple") and preset.triple:
            target_flag = f"--target={preset.triple}"
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.append(target_flag)

        # Delegate to base for sysroot, extra flags, env_vars, arch
        super().apply_cross_preset(env, preset)

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply build variant (debug, release, etc.).

        Args:
            env: Environment to modify.
            variant: Variant name (debug, release, relwithdebinfo, minsizerel).
            **kwargs: Optional extra_flags and extra_defines to add.
        """
        super().apply_variant(env, variant, **kwargs)

        compile_flags: list[str] = []
        defines: list[str] = []
        link_flags: list[str] = []

        variant_lower = variant.lower()
        if variant_lower == "debug":
            compile_flags = ["-O0", "-g"]
            defines = ["DEBUG", "_DEBUG"]
        elif variant_lower == "release":
            compile_flags = ["-O2"]
            defines = ["NDEBUG"]
        elif variant_lower == "relwithdebinfo":
            compile_flags = ["-O2", "-g"]
            defines = ["NDEBUG"]
        elif variant_lower == "minsizerel":
            compile_flags = ["-Os"]
            defines = ["NDEBUG"]
        else:
            logger.warning("Unknown variant '%s', no flags applied", variant)

        # Add extra flags/defines from kwargs
        extra_flags = kwargs.get("extra_flags", [])
        extra_defines = kwargs.get("extra_defines", [])
        compile_flags.extend(extra_flags)
        defines.extend(extra_defines)

        # Apply to compilers
        for tool_name in ("cc", "cxx"):
            if env.has_tool(tool_name):
                tool = getattr(env, tool_name)
                if hasattr(tool, "flags") and isinstance(tool.flags, list):
                    tool.flags.extend(compile_flags)
                if hasattr(tool, "defines") and isinstance(tool.defines, list):
                    tool.defines.extend(defines)

        # Apply to linker
        if env.has_tool("link") and link_flags:
            if isinstance(env.link.flags, list):
                env.link.flags.extend(link_flags)
