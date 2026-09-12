# SPDX-License-Identifier: MIT
"""Tests for toolchain build variants.

Each toolchain implements its own apply_variant() method to handle
build variants like "debug" and "release". The core only knows
the variant name - toolchains define what it means.

Note: Defines are stored without the -D prefix (e.g., "DEBUG" not "-DDEBUG").
The prefix is applied during expansion via ${prefix(dprefix, defines)}.
"""

from __future__ import annotations

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain


class TestGccVariants:
    """Tests for GCC toolchain variants."""

    def test_debug_variant(self, test_project):  # noqa: F811
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

    def test_release_variant(self, test_project):  # noqa: F811
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

    def test_release_fastest_variant(self, test_project):  # noqa: F811
        """release-fastest is the highest safe level: -O3, and no fast-math."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "release-fastest")

        assert "-O3" in cc.flags
        assert not any(f.startswith("-ffast-math") for f in cc.flags)
        assert "NDEBUG" in cc.defines
        assert env.variant == "release-fastest"

    def test_relwithdebinfo_variant(self, test_project):  # noqa: F811
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

    def test_minsizerel_variant(self, test_project):  # noqa: F811
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

    def test_extra_flags(self, test_project):  # noqa: F811
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

    def test_extra_defines(self, test_project):  # noqa: F811
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

    def test_unknown_variant(self, test_project):  # noqa: F811
        """Test unknown variant sets name but no flags."""
        env = Environment()

        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        # Unknown variants now raise ValueError with supported variant list
        with pytest.raises(ValueError, match="Unknown variant.*custom"):
            toolchain.apply_variant(env, "custom")

    def test_case_insensitive(self, test_project):  # noqa: F811
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

    def test_debug_variant(self, test_project):  # noqa: F811
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

    def test_release_variant(self, test_project):  # noqa: F811
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

    def test_set_variant_without_toolchain(self, test_project):  # noqa: F811
        """Test set_variant without toolchain just sets name."""
        env = Environment()

        env.set_variant("debug")

        assert env.variant == "debug"

    def test_set_variant_with_toolchain(self, test_project):  # noqa: F811
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

    def test_preserves_existing_flags(self, test_project):  # noqa: F811
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

    def test_base_sets_variant_name(self, test_project):  # noqa: F811
        """Test base implementation sets variant name."""

        # Can't instantiate abstract class directly, use a concrete one
        # and verify the base behavior (super().apply_variant sets env.variant)
        env = Environment()
        env.add_tool("cc").set("flags", [])
        env.add_tool("cc").set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_variant(env, "debug")

        assert env.variant == "debug"

    def test_base_rejects_unknown_variant(self, test_project):  # noqa: F811
        """Test that unknown variant names are rejected with a helpful error."""
        env = Environment()

        toolchain = GccToolchain()
        with pytest.raises(ValueError, match="Unknown variant.*custom"):
            toolchain.apply_variant(env, "custom")


class TestClangClVariants:
    """The MSVC-style realization of the same variant names."""

    def test_release_fastest_variant(self, test_project):  # noqa: F811
        from pcons.toolchains.clang_cl import ClangClToolchain

        env = Environment()
        for tool in ("cc", "cxx"):
            cfg = env.add_tool(tool)
            cfg.set("cmd", "clang-cl")
            cfg.set("flags", [])
            cfg.set("defines", [])

        ClangClToolchain().apply_variant(env, "release-fastest")

        assert "/O2" in env.cc.flags
        assert "/Ob3" in env.cc.flags
        assert "/MD" in env.cc.flags
        assert "NDEBUG" in env.cxx.defines

    @pytest.mark.parametrize(
        ("variant", "crt"),
        [("debug", "/MDd"), ("release", "/MD"), ("relwithdebinfo", "/MD")],
    )
    def test_variant_selects_matching_dynamic_crt(
        self, test_project, variant: str, crt: str
    ):  # noqa: F811
        """A variant names its CRT: cl.exe's default static release CRT
        with the debug variant's _DEBUG pairs with the debug STL and fails
        to link, and Conan packages are built against the dynamic one."""
        from pcons.toolchains.msvc import MsvcToolchain

        env = Environment()
        for tool in ("cc", "cxx"):
            cfg = env.add_tool(tool)
            cfg.set("cmd", "cl.exe")
            cfg.set("flags", [])
            cfg.set("defines", [])

        MsvcToolchain().apply_variant(env, variant)

        assert crt in env.cc.flags
        assert crt in env.cxx.flags
        assert len([f for f in env.cxx.flags if f.startswith("/M")]) == 1

    @pytest.mark.parametrize("toolchain_name", ["msvc", "clang-cl"])
    @pytest.mark.parametrize(
        ("variant", "has_debug_info"),
        [("debug", True), ("relwithdebinfo", True), ("release", False)],
    )
    def test_debug_info_lands_in_objects_and_the_linker_writes_the_pdb(
        self, test_project, toolchain_name: str, variant: str, has_debug_info: bool
    ):  # noqa: F811
        """/Z7 keeps debug info in each object (no shared compiler PDB for
        parallel cl.exe to fight over); the linker's /DEBUG then produces
        the PDB. Both toolchains, since clang-cl links with the same flags."""
        from pcons.toolchains.clang_cl import ClangClToolchain
        from pcons.toolchains.msvc import MsvcToolchain

        env = Environment()
        for tool in ("cc", "cxx", "link"):
            cfg = env.add_tool(tool)
            cfg.set("cmd", "x")
            cfg.set("flags", [])
            cfg.set("defines", [])
        toolchain = MsvcToolchain() if toolchain_name == "msvc" else ClangClToolchain()

        toolchain.apply_variant(env, variant)

        assert ("/Z7" in env.cxx.flags) is has_debug_info
        assert ("/DEBUG" in env.link.flags) is has_debug_info
        assert "/Zi" not in env.cxx.flags
