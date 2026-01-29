# SPDX-License-Identifier: MIT
"""Tests for pcons.core.environment."""

from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.core.toolconfig import ToolConfig


class TestEnvironmentBasic:
    def test_creation(self):
        env = Environment()
        assert env.defined_at is not None

    def test_default_build_dir(self):
        env = Environment()
        assert env.build_dir == Path("build")

    def test_set_cross_tool_var(self):
        env = Environment()
        env.variant = "release"
        assert env.variant == "release"

    def test_get_missing_raises(self):
        env = Environment()
        with pytest.raises(AttributeError) as exc_info:
            _ = env.missing
        assert "missing" in str(exc_info.value)

    def test_get_with_default(self):
        env = Environment()
        assert env.get("missing") is None
        assert env.get("missing", "default") == "default"


class TestEnvironmentTools:
    def test_add_tool(self):
        env = Environment()
        cc = env.add_tool("cc")
        assert isinstance(cc, ToolConfig)
        assert cc.name == "cc"

    def test_add_tool_with_config(self):
        env = Environment()
        config = ToolConfig("cc", cmd="gcc")
        cc = env.add_tool("cc", config)
        assert cc is config
        assert env.cc.cmd == "gcc"

    def test_add_existing_tool_returns_it(self):
        env = Environment()
        cc1 = env.add_tool("cc")
        cc1.cmd = "gcc"
        cc2 = env.add_tool("cc")
        assert cc1 is cc2
        assert cc2.cmd == "gcc"

    def test_has_tool(self):
        env = Environment()
        assert not env.has_tool("cc")
        env.add_tool("cc")
        assert env.has_tool("cc")

    def test_tool_names(self):
        env = Environment()
        env.add_tool("cc")
        env.add_tool("cxx")
        names = env.tool_names()
        assert "cc" in names
        assert "cxx" in names

    def test_access_tool_via_attribute(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        assert env.cc.cmd == "gcc"

    def test_tool_takes_precedence_over_var(self):
        env = Environment()
        env.cc = "variable_value"  # Set as variable
        tool_config = env.add_tool("cc")  # Now add tool
        tool_config.cmd = "gcc"
        # Tool should take precedence
        assert isinstance(env.cc, ToolConfig)


class TestEnvironmentClone:
    def test_clone_basic(self):
        env = Environment()
        env.variant = "debug"
        clone = env.clone()
        assert clone.variant == "debug"

    def test_clone_is_independent(self):
        env = Environment()
        env.variant = "debug"
        clone = env.clone()

        clone.variant = "release"
        assert env.variant == "debug"
        assert clone.variant == "release"

    def test_clone_deep_copies_tools(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.cc.flags = ["-Wall"]

        clone = env.clone()
        clone.cc.cmd = "clang"
        clone.cc.flags.append("-O2")

        assert env.cc.cmd == "gcc"
        assert env.cc.flags == ["-Wall"]
        assert clone.cc.cmd == "clang"
        assert clone.cc.flags == ["-Wall", "-O2"]


class TestEnvironmentSubst:
    def test_subst_cross_tool_var(self):
        env = Environment()
        env.name = "myapp"
        result = env.subst("Building $name")
        assert result == "Building myapp"

    def test_subst_tool_var(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        result = env.subst("Compiler: $cc.cmd")
        # subst() returns a shell command string (space-separated)
        assert "Compiler:" in result
        assert "gcc" in result

    def test_subst_with_extra(self):
        env = Environment()
        result = env.subst("Target: $target", target="app.exe")
        assert "Target:" in result
        assert "app.exe" in result

    def test_subst_list(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.flags = ["-Wall", "-O2"]
        result = env.subst_list("$cc.flags")
        # subst_list() returns a list of tokens
        assert result == ["-Wall", "-O2"]

    def test_subst_list_with_string(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.flags = "-Wall -O2"
        result = env.subst_list("$cc.flags")
        # Single token stays as a single token
        assert result == ["-Wall -O2"]

    def test_subst_list_string_tokenized(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.flags = "-Wall -O2"
        # Using a string template that gets tokenized first
        result = env.subst_list("$cc.flags more flags")
        # String template tokenizes, then $cc.flags stays as one token
        assert result == ["-Wall -O2", "more", "flags"]

    def test_subst_complex(self):
        # For complex command templates with list variables, use list templates
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.cc.flags = ["-Wall", "-O2"]

        # List template properly expands list variables
        result = env.subst_list(
            ["$cc.cmd", "$cc.flags", "-c", "-o", "$out", "$src"],
            out="foo.o",
            src="foo.c",
        )
        assert "gcc" in result
        assert "-Wall" in result
        assert "-O2" in result
        assert "foo.o" in result
        assert "foo.c" in result

    def test_subst_list_template(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.cc.flags = ["-Wall", "-O2"]

        result = env.subst_list(["$cc.cmd", "$cc.flags", "-c", "file.c"])
        assert result == ["gcc", "-Wall", "-O2", "-c", "file.c"]

    def test_subst_with_prefix_function(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.cc.iprefix = "-I"
        env.cc.includes = ["src", "include"]

        result = env.subst_list(["$cc.cmd", "${prefix(cc.iprefix, cc.includes)}"])
        assert result == ["gcc", "-Isrc", "-Iinclude"]


class TestEnvironmentOverride:
    """Tests for env.override() context manager."""

    def test_override_simple_var(self):
        """Override a simple variable."""
        env = Environment()
        env.variant = "release"

        with env.override(variant="debug") as temp_env:
            assert temp_env.variant == "debug"
            assert env.variant == "release"  # Original unchanged

        assert env.variant == "release"

    def test_override_tool_setting(self):
        """Override tool settings using double-underscore notation."""
        env = Environment()
        env.add_tool("cc")
        env.cc.flags = ["-Wall"]

        with env.override(cc__flags=["-Wall", "-Werror"]) as temp_env:
            assert temp_env.cc.flags == ["-Wall", "-Werror"]
            assert env.cc.flags == ["-Wall"]  # Original unchanged

    def test_override_add_define(self):
        """Common use case: add a specific define for some files."""
        env = Environment()
        env.add_tool("cxx")
        env.cxx.defines = ["RELEASE"]

        with env.override(cxx__defines=["RELEASE", "SPECIAL_BUILD"]) as temp_env:
            assert "SPECIAL_BUILD" in temp_env.cxx.defines
            assert "SPECIAL_BUILD" not in env.cxx.defines

    def test_override_multiple_settings(self):
        """Override multiple settings at once."""
        env = Environment()
        env.variant = "release"
        env.add_tool("cc")
        env.cc.cmd = "gcc"

        with env.override(variant="debug", cc__cmd="clang") as temp_env:
            assert temp_env.variant == "debug"
            assert temp_env.cc.cmd == "clang"

    def test_override_returns_clone(self):
        """Override returns a cloned environment, not the original."""
        env = Environment()
        env.variant = "release"

        with env.override(variant="debug") as temp_env:
            assert temp_env is not env


class TestCompilerCache:
    """Tests for use_compiler_cache()."""

    def test_auto_detect_skips_when_not_found(self) -> None:
        """Auto-detect should be a no-op when no cache tool is found."""
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cxx = env.add_tool("cxx")
        cxx.set("cmd", "g++")

        # This should not raise; if neither ccache nor sccache is
        # installed, it logs a warning and returns.
        env.use_compiler_cache()
        # cmd might or might not be wrapped depending on the test machine

    def test_explicit_tool_wraps_commands(self) -> None:
        """Explicit tool name should wrap cc and cxx commands."""
        import shutil

        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        cxx = env.add_tool("cxx")
        cxx.set("cmd", "g++")

        # Only test if a cache tool is available
        tool = None
        for candidate in ("ccache", "sccache"):
            if shutil.which(candidate):
                tool = candidate
                break

        if tool is None:
            pytest.skip("No compiler cache tool available")

        env.use_compiler_cache(tool)

        assert env.cc.cmd.startswith(tool)
        assert env.cxx.cmd.startswith(tool)
        # Original command should still be there
        assert "gcc" in env.cc.cmd
        assert "g++" in env.cxx.cmd

    def test_no_double_wrapping(self) -> None:
        """Should not double-wrap if already prefixed."""
        import shutil

        tool = None
        for candidate in ("ccache", "sccache"):
            if shutil.which(candidate):
                tool = candidate
                break

        if tool is None:
            pytest.skip("No compiler cache tool available")

        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", f"{tool} gcc")

        env.use_compiler_cache(tool)

        # Should not be double-wrapped
        assert env.cc.cmd == f"{tool} gcc"

    def test_skips_tools_not_present(self) -> None:
        """Should skip tools that don't exist."""
        import shutil

        tool = None
        for candidate in ("ccache", "sccache"):
            if shutil.which(candidate):
                tool = candidate
                break

        if tool is None:
            pytest.skip("No compiler cache tool available")

        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")
        # No cxx tool

        env.use_compiler_cache(tool)
        assert env.cc.cmd.startswith(tool)

    def test_unknown_tool_warns(self) -> None:
        """Unknown tool name should warn and not modify."""
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")

        env.use_compiler_cache("nonexistent-cache-tool")
        assert env.cc.cmd == "gcc"

    def test_missing_explicit_tool_warns(self) -> None:
        """Explicit tool not in PATH should warn and not modify."""
        import shutil

        env = Environment()
        cc = env.add_tool("cc")
        cc.set("cmd", "gcc")

        # Use a tool name that's valid but not installed
        if shutil.which("ccache") is None:
            env.use_compiler_cache("ccache")
            assert env.cc.cmd == "gcc"
        elif shutil.which("sccache") is None:
            env.use_compiler_cache("sccache")
            assert env.cc.cmd == "gcc"
        else:
            # Both installed, skip this test
            pytest.skip("Both ccache and sccache installed")


class TestEnvironmentRepr:
    def test_repr(self):
        env = Environment()
        env.add_tool("cc")
        r = repr(env)
        assert "Environment" in r
        assert "cc" in r
