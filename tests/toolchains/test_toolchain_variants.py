# SPDX-License-Identifier: MIT
"""Tests for toolchain build variants.

Each toolchain implements its own apply_variant() method to handle
build variants like "debug" and "release". The core only knows
the variant name - toolchains define what it means.

Note: Defines are stored without the -D prefix (e.g., "DEBUG" not "-DDEBUG").
The prefix is applied during expansion via ${prefix(dprefix, defines)}.
"""

from __future__ import annotations

from pcons.core.environment import Environment
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain


class TestGccVariants:
    """Tests for GCC toolchain variants."""

    def test_debug_variant(self) -> None:
        """Test GCC debug variant applies correct flags."""
        env = Environment()

        # Set up a mock GCC toolchain (just add the tools manually)
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        cxx = env.add_tool("cxx")
        cxx.set("cmd", "g++")
        cxx.set("flags", [])
        cxx.set("defines", [])

        link = env.add_tool("link")
        link.set("cmd", "gcc")
        link.set("flags", [])

        # Create toolchain and apply variant
        toolchain = GccToolchain()
        toolchain.apply_variant(env, "debug")

        # Check flags were applied
        assert "-O0" in cc.flags
        assert "-g" in cc.flags
        # Defines stored without -D prefix
        assert "DEBUG" in cc.defines
        assert "_DEBUG" in cc.defines

        assert "-O0" in cxx.flags
        assert "-g" in cxx.flags

        # Check variant name was set
        assert env.variant == "debug"

    def test_release_variant(self) -> None:
        """Test GCC release variant applies correct flags."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "release")

        assert "-O2" in cc.flags
        assert "-g" not in cc.flags
        # Define stored without -D prefix
        assert "NDEBUG" in cc.defines
        assert env.variant == "release"

    def test_relwithdebinfo_variant(self) -> None:
        """Test GCC relwithdebinfo variant."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "relwithdebinfo")

        assert "-O2" in cc.flags
        assert "-g" in cc.flags
        assert "NDEBUG" in cc.defines

    def test_minsizerel_variant(self) -> None:
        """Test GCC minsizerel variant."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "minsizerel")

        assert "-Os" in cc.flags
        assert "NDEBUG" in cc.defines

    def test_extra_flags(self) -> None:
        """Test extra flags are added."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "debug", extra_flags=["-Wall", "-Wextra"])

        assert "-Wall" in cc.flags
        assert "-Wextra" in cc.flags

    def test_extra_defines(self) -> None:
        """Test extra defines are added (without -D prefix)."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        # Extra defines stored without prefix
        toolchain.apply_variant(env, "release", extra_defines=["MY_FEATURE"])

        assert "MY_FEATURE" in cc.defines

    def test_unknown_variant(self) -> None:
        """Test unknown variant sets name but no flags."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "custom")

        # Variant name should be set
        assert env.variant == "custom"
        # No flags should be added for unknown variant
        assert len(cc.flags) == 0
        assert len(cc.defines) == 0

    def test_case_insensitive(self) -> None:
        """Test variant names are case-insensitive."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "DEBUG")

        assert "-O0" in cc.flags
        assert "-g" in cc.flags


class TestLlvmVariants:
    """Tests for LLVM/Clang toolchain variants."""

    def test_debug_variant(self) -> None:
        """Test LLVM debug variant."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = LlvmToolchain()
        toolchain.apply_variant(env, "debug")

        assert "-O0" in cc.flags
        assert "-g" in cc.flags
        # Defines stored without -D prefix
        assert "DEBUG" in cc.defines
        assert env.variant == "debug"

    def test_release_variant(self) -> None:
        """Test LLVM release variant."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "clang")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = LlvmToolchain()
        toolchain.apply_variant(env, "release")

        assert "-O2" in cc.flags
        assert "NDEBUG" in cc.defines


class TestEnvironmentSetVariant:
    """Tests for Environment.set_variant() method."""

    def test_set_variant_without_toolchain(self) -> None:
        """Test set_variant without toolchain just sets name."""
        env = Environment()

        env.set_variant("debug")

        assert env.variant == "debug"

    def test_set_variant_with_toolchain(self) -> None:
        """Test set_variant delegates to toolchain."""
        # Create env with tools set up manually
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        # Set toolchain manually
        toolchain = GccToolchain()
        env._toolchain = toolchain

        env.set_variant("debug")

        # Should have applied GCC debug flags
        assert "-O0" in cc.flags
        assert "-g" in cc.flags
        assert env.variant == "debug"

    def test_preserves_existing_flags(self) -> None:
        """Test that set_variant preserves existing flags."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", ["-Wall", "-Wextra"])
        # Existing defines without prefix
        cc.set("defines", ["FOO"])

        toolchain = GccToolchain()
        env._toolchain = toolchain

        env.set_variant("debug")

        # Original flags should still be there
        assert "-Wall" in cc.flags
        assert "-Wextra" in cc.flags
        assert "FOO" in cc.defines
        # New flags should be added
        assert "-O0" in cc.flags
        assert "-g" in cc.flags


class TestBaseToolchainVariant:
    """Tests for BaseToolchain default apply_variant."""

    def test_base_sets_variant_name(self) -> None:
        """Test base implementation sets variant name."""

        # Can't instantiate abstract class directly, use a concrete one
        # and verify the base behavior
        env = Environment()

        toolchain = GccToolchain()
        # Call parent's apply_variant indirectly through subclass
        # The subclass calls super().apply_variant() which sets env.variant
        toolchain.apply_variant(env, "custom")

        assert env.variant == "custom"
