# SPDX-License-Identifier: MIT
"""Tests for toolchain target architecture support.

Each toolchain implements its own apply_target_arch() method to handle
target architectures for cross-compilation (e.g., macOS universal binaries,
Windows multi-arch builds). The core only knows the arch name - toolchains
define what flags it means.
"""

from __future__ import annotations

from unittest.mock import patch

from pcons.core.environment import Environment
from pcons.toolchains.clang_cl import ClangClToolchain
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain
from pcons.toolchains.msvc import MsvcToolchain


class TestGccTargetArch:
    """Tests for GCC toolchain target architecture."""

    def test_macos_arm64(self) -> None:
        """Test GCC arm64 target on macOS adds -arch flag."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        cxx = env.add_tool("cxx")
        cxx.set("cmd", "g++")
        cxx.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        toolchain = GccToolchain()

        # Mock macOS platform
        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = True
            mock_platform.return_value.is_linux = False
            mock_platform.return_value.is_posix = True

            toolchain.apply_target_arch(env, "arm64")

        # Check -arch flags were applied to compiler and linker
        assert "-arch" in cc.flags
        assert "arm64" in cc.flags
        assert "-arch" in cxx.flags
        assert "arm64" in cxx.flags
        assert "-arch" in link.flags
        assert "arm64" in link.flags

    def test_macos_x86_64(self) -> None:
        """Test GCC x86_64 target on macOS adds -arch flag."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        toolchain = GccToolchain()

        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = True
            mock_platform.return_value.is_linux = False
            mock_platform.return_value.is_posix = True

            toolchain.apply_target_arch(env, "x86_64")

        assert "-arch" in cc.flags
        assert "x86_64" in cc.flags
        assert "-arch" in link.flags
        assert "x86_64" in link.flags

    def test_linux_no_arch_flags(self) -> None:
        """Test GCC on Linux doesn't add -arch flags (requires cross-toolchain)."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        toolchain = GccToolchain()

        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = False
            mock_platform.return_value.is_linux = True
            mock_platform.return_value.is_posix = True

            toolchain.apply_target_arch(env, "arm64")

        # Linux GCC doesn't use -arch flags (need cross-compiler instead)
        assert "-arch" not in cc.flags
        assert "-arch" not in link.flags


class TestLlvmTargetArch:
    """Tests for LLVM/Clang toolchain target architecture."""

    def test_macos_arm64(self) -> None:
        """Test LLVM arm64 target on macOS adds -arch flag."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang")
        cc.set("flags", [])

        cxx = env.add_tool("cxx")
        cxx.set("cmd", "clang++")
        cxx.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "clang")
        link.set("flags", [])

        toolchain = LlvmToolchain()

        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = True
            mock_platform.return_value.is_linux = False
            mock_platform.return_value.is_posix = True

            toolchain.apply_target_arch(env, "arm64")

        # Check -arch flags were applied
        assert "-arch" in cc.flags
        assert "arm64" in cc.flags
        assert "-arch" in cxx.flags
        assert "arm64" in cxx.flags
        assert "-arch" in link.flags
        assert "arm64" in link.flags


class TestMsvcTargetArch:
    """Tests for MSVC toolchain target architecture."""

    def test_x64_machine_flag(self) -> None:
        """Test MSVC x64 target adds /MACHINE:X64."""
        env = Environment()

        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        lib = env.add_tool("lib")
        lib.set("cmd", "lib.exe")
        lib.set("flags", [])

        toolchain = MsvcToolchain()
        toolchain.apply_target_arch(env, "x64")

        assert "/MACHINE:X64" in link.flags
        assert "/MACHINE:X64" in lib.flags

    def test_arm64_machine_flag(self) -> None:
        """Test MSVC arm64 target adds /MACHINE:ARM64."""
        env = Environment()

        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        lib = env.add_tool("lib")
        lib.set("cmd", "lib.exe")
        lib.set("flags", [])

        toolchain = MsvcToolchain()
        toolchain.apply_target_arch(env, "arm64")

        assert "/MACHINE:ARM64" in link.flags
        assert "/MACHINE:ARM64" in lib.flags

    def test_x86_machine_flag(self) -> None:
        """Test MSVC x86 target adds /MACHINE:X86."""
        env = Environment()

        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        toolchain = MsvcToolchain()
        toolchain.apply_target_arch(env, "x86")

        assert "/MACHINE:X86" in link.flags

    def test_arm64ec_machine_flag(self) -> None:
        """Test MSVC arm64ec target adds /MACHINE:ARM64EC."""
        env = Environment()

        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        toolchain = MsvcToolchain()
        toolchain.apply_target_arch(env, "arm64ec")

        assert "/MACHINE:ARM64EC" in link.flags

    def test_arch_aliases(self) -> None:
        """Test MSVC architecture aliases are mapped correctly."""
        env = Environment()

        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        toolchain = MsvcToolchain()

        # Test various aliases
        env.link.flags = []
        toolchain.apply_target_arch(env, "amd64")
        assert "/MACHINE:X64" in link.flags

        env.link.flags = []
        toolchain.apply_target_arch(env, "x86_64")
        assert "/MACHINE:X64" in link.flags

        env.link.flags = []
        toolchain.apply_target_arch(env, "aarch64")
        assert "/MACHINE:ARM64" in link.flags


class TestClangClTargetArch:
    """Tests for Clang-CL toolchain target architecture."""

    def test_x64_target_and_machine(self) -> None:
        """Test Clang-CL x64 target adds --target flag and /MACHINE."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang-cl")
        cc.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "lld-link")
        link.set("flags", [])

        lib = env.add_tool("lib")
        lib.set("cmd", "llvm-lib")
        lib.set("flags", [])

        toolchain = ClangClToolchain()
        toolchain.apply_target_arch(env, "x64")

        # Compiler gets --target
        assert "--target=x86_64-pc-windows-msvc" in cc.flags
        # Linker gets /MACHINE
        assert "/MACHINE:X64" in link.flags
        assert "/MACHINE:X64" in lib.flags

    def test_arm64_target_and_machine(self) -> None:
        """Test Clang-CL arm64 target adds --target flag and /MACHINE."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang-cl")
        cc.set("flags", [])

        cxx = env.add_tool("cxx")
        cxx.set("cmd", "clang-cl")
        cxx.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "lld-link")
        link.set("flags", [])

        toolchain = ClangClToolchain()
        toolchain.apply_target_arch(env, "arm64")

        assert "--target=aarch64-pc-windows-msvc" in cc.flags
        assert "--target=aarch64-pc-windows-msvc" in cxx.flags
        assert "/MACHINE:ARM64" in link.flags

    def test_x86_target_and_machine(self) -> None:
        """Test Clang-CL x86 target adds --target flag and /MACHINE."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang-cl")
        cc.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "lld-link")
        link.set("flags", [])

        toolchain = ClangClToolchain()
        toolchain.apply_target_arch(env, "x86")

        assert "--target=i686-pc-windows-msvc" in cc.flags
        assert "/MACHINE:X86" in link.flags


class TestEnvironmentSetTargetArch:
    """Tests for Environment.set_target_arch() method."""

    def test_set_target_arch_stores_name(self) -> None:
        """Test set_target_arch stores the architecture name."""
        env = Environment()

        env.set_target_arch("arm64")

        assert env.target_arch == "arm64"

    def test_set_target_arch_with_toolchain(self) -> None:
        """Test set_target_arch delegates to toolchain."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        # Set toolchain manually
        toolchain = GccToolchain()
        env._toolchain = toolchain

        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = True
            mock_platform.return_value.is_linux = False
            mock_platform.return_value.is_posix = True

            env.set_target_arch("arm64")

        # Should have applied GCC arch flags
        assert "-arch" in cc.flags
        assert "arm64" in cc.flags
        assert env.target_arch == "arm64"

    def test_orthogonal_to_variant(self) -> None:
        """Test that set_target_arch is orthogonal to set_variant."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        toolchain = GccToolchain()
        env._toolchain = toolchain

        # Apply variant first
        toolchain.apply_variant(env, "release")

        # Then apply target arch
        with patch("pcons.toolchains.unix.get_platform") as mock_platform:
            mock_platform.return_value.is_macos = True
            mock_platform.return_value.is_linux = False
            mock_platform.return_value.is_posix = True

            env.set_target_arch("arm64")

        # Should have both variant flags and arch flags
        assert "-O2" in cc.flags  # From release variant
        assert "-arch" in cc.flags  # From target arch
        assert "arm64" in cc.flags
        assert "NDEBUG" in cc.defines  # From release variant
        assert env.variant == "release"
        assert env.target_arch == "arm64"


class TestBaseToolchainTargetArch:
    """Tests for BaseToolchain default apply_target_arch."""

    def test_base_is_noop(self) -> None:
        """Test base implementation is a no-op (doesn't add flags)."""
        from pcons.tools.toolchain import BaseToolchain

        # Create a minimal concrete subclass for testing
        class MinimalToolchain(BaseToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])

        toolchain = MinimalToolchain("minimal")
        toolchain.apply_target_arch(env, "arm64")

        # Base implementation should not add any flags
        assert len(cc.flags) == 0
