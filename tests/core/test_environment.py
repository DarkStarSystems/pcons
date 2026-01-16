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
        env.add_tool("cc")  # Now add tool
        env.cc.cmd = "gcc"
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
        assert result == "Compiler: gcc"

    def test_subst_with_extra(self):
        env = Environment()
        result = env.subst("Target: $target", target="app.exe")
        assert result == "Target: app.exe"

    def test_subst_list(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.flags = "-Wall -O2"
        result = env.subst_list("$cc.flags")
        assert result == ["-Wall", "-O2"]

    def test_subst_complex(self):
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "gcc"
        env.cc.flags = ["-Wall", "-O2"]
        env.cc.cmdline = "$cc.cmd $cc.flags -c -o $out $src"

        result = env.subst("$cc.cmdline", out="foo.o", src="foo.c")
        assert "gcc" in result
        assert "-Wall -O2" in result
        assert "foo.o" in result
        assert "foo.c" in result


class TestEnvironmentRepr:
    def test_repr(self):
        env = Environment()
        env.add_tool("cc")
        r = repr(env)
        assert "Environment" in r
        assert "cc" in r
