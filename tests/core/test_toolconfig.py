# SPDX-License-Identifier: MIT
"""Tests for pcons.core.toolconfig."""

import pytest

from pcons.core.toolconfig import ToolConfig


class TestToolConfigBasic:
    def test_creation(self):
        tc = ToolConfig("cc")
        assert tc.name == "cc"

    def test_creation_with_defaults(self):
        tc = ToolConfig("cc", cmd="gcc", flags=["-Wall"])
        assert tc.cmd == "gcc"
        assert tc.flags == ["-Wall"]

    def test_set_and_get_attribute(self):
        tc = ToolConfig("cc")
        tc.cmd = "gcc"
        tc.flags = ["-Wall", "-O2"]
        assert tc.cmd == "gcc"
        assert tc.flags == ["-Wall", "-O2"]

    def test_get_missing_attribute(self):
        tc = ToolConfig("cc")
        with pytest.raises(AttributeError) as exc_info:
            _ = tc.missing
        assert "missing" in str(exc_info.value)
        assert "cc" in str(exc_info.value)

    def test_get_with_default(self):
        tc = ToolConfig("cc")
        assert tc.get("missing") is None
        assert tc.get("missing", "default") == "default"
        tc.cmd = "gcc"
        assert tc.get("cmd") == "gcc"

    def test_set_method(self):
        tc = ToolConfig("cc")
        tc.set("cmd", "gcc")
        assert tc.cmd == "gcc"

    def test_contains(self):
        tc = ToolConfig("cc", cmd="gcc")
        assert "cmd" in tc
        assert "missing" not in tc

    def test_iteration(self):
        tc = ToolConfig("cc", cmd="gcc", flags=["-Wall"])
        names = list(tc)
        assert "cmd" in names
        assert "flags" in names

    def test_delete_attribute(self):
        tc = ToolConfig("cc", cmd="gcc")
        assert "cmd" in tc
        del tc.cmd
        assert "cmd" not in tc

    def test_delete_missing_raises(self):
        tc = ToolConfig("cc")
        with pytest.raises(AttributeError):
            del tc.missing


class TestToolConfigClone:
    def test_clone_basic(self):
        tc = ToolConfig("cc", cmd="gcc")
        clone = tc.clone()
        assert clone.name == "cc"
        assert clone.cmd == "gcc"

    def test_clone_is_independent(self):
        tc = ToolConfig("cc", cmd="gcc", flags=["-Wall"])
        clone = tc.clone()

        # Modify original
        tc.cmd = "clang"
        tc.flags.append("-O2")  # This modifies original list

        # Clone should be unchanged
        assert clone.cmd == "gcc"

    def test_clone_deep_copies_lists(self):
        tc = ToolConfig("cc", flags=["-Wall"])
        clone = tc.clone()

        # Modify clone's list
        clone.flags.append("-O2")

        # Original should be unchanged
        assert tc.flags == ["-Wall"]
        assert clone.flags == ["-Wall", "-O2"]

    def test_clone_deep_copies_dicts(self):
        tc = ToolConfig("cc", options={"debug": True})
        clone = tc.clone()

        clone.options["debug"] = False

        assert tc.options["debug"] is True
        assert clone.options["debug"] is False


class TestToolConfigUpdate:
    def test_update(self):
        tc = ToolConfig("cc")
        tc.update({"cmd": "gcc", "flags": ["-Wall"]})
        assert tc.cmd == "gcc"
        assert tc.flags == ["-Wall"]


class TestToolConfigNamespace:
    def test_as_dict(self):
        tc = ToolConfig("cc", cmd="gcc", flags=["-Wall"])
        d = tc.as_dict()
        assert d == {"cmd": "gcc", "flags": ["-Wall"]}

    def test_as_namespace(self):
        tc = ToolConfig("cc", cmd="gcc", flags=["-Wall"])
        ns = tc.as_namespace()
        assert ns["cmd"] == "gcc"
        assert ns["flags"] == ["-Wall"]
        # as_namespace returns a copy to prevent accidental mutation
        # during variable substitution
        ns["cmd"] = "clang"
        ns["flags"].append("-O2")
        # Original should be unchanged
        assert tc.cmd == "gcc"
        assert tc.flags == ["-Wall"]

    def test_repr(self):
        tc = ToolConfig("cc", cmd="gcc")
        r = repr(tc)
        assert "ToolConfig" in r
        assert "cc" in r
        assert "gcc" in r
