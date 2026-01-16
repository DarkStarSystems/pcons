# SPDX-License-Identifier: MIT
"""Tests for pcons.core.subst."""

import pytest

from pcons.core.errors import CircularReferenceError, MissingVariableError
from pcons.core.subst import Namespace, escape, subst, subst_list


class TestNamespace:
    def test_basic_get_set(self):
        ns = Namespace()
        ns["foo"] = "bar"
        assert ns["foo"] == "bar"
        assert ns.get("foo") == "bar"

    def test_missing_key(self):
        ns = Namespace()
        assert ns.get("missing") is None
        assert ns.get("missing", "default") == "default"
        with pytest.raises(KeyError):
            _ = ns["missing"]

    def test_contains(self):
        ns = Namespace({"foo": "bar"})
        assert "foo" in ns
        assert "missing" not in ns

    def test_dotted_access(self):
        ns = Namespace({"cc": {"cmd": "gcc", "flags": ["-Wall"]}})
        assert ns["cc.cmd"] == "gcc"
        assert ns.get("cc.flags") == ["-Wall"]

    def test_dotted_set(self):
        ns = Namespace()
        ns["cc.cmd"] = "gcc"
        ns["cc.flags"] = ["-Wall"]
        assert ns["cc.cmd"] == "gcc"
        assert ns["cc.flags"] == ["-Wall"]

    def test_nested_namespace(self):
        inner = Namespace({"cmd": "gcc"})
        outer = Namespace({"cc": inner})
        assert outer["cc.cmd"] == "gcc"

    def test_parent_fallback(self):
        parent = Namespace({"CC": "gcc"})
        child = Namespace({"CFLAGS": "-Wall"}, parent=parent)
        assert child["CFLAGS"] == "-Wall"
        assert child["CC"] == "gcc"  # Falls back to parent

    def test_update(self):
        ns = Namespace({"a": 1})
        ns.update({"b": 2, "c": 3})
        assert ns["a"] == 1
        assert ns["b"] == 2
        assert ns["c"] == 3


class TestSubstSimple:
    def test_no_variables(self):
        result = subst("hello world", {})
        assert result == "hello world"

    def test_simple_variable(self):
        result = subst("hello $name", {"name": "world"})
        assert result == "hello world"

    def test_braced_variable(self):
        result = subst("hello ${name}", {"name": "world"})
        assert result == "hello world"

    def test_multiple_variables(self):
        result = subst("$a and $b", {"a": "foo", "b": "bar"})
        assert result == "foo and bar"

    def test_adjacent_variables(self):
        result = subst("$a$b$c", {"a": "1", "b": "2", "c": "3"})
        assert result == "123"

    def test_escaped_dollar(self):
        result = subst("price is $$10", {})
        assert result == "price is $10"

    def test_double_escape(self):
        result = subst("$$$$", {})
        assert result == "$$"


class TestSubstNamespaced:
    def test_dotted_variable(self):
        ns = {"cc": {"cmd": "gcc", "flags": "-Wall"}}
        result = subst("$cc.cmd $cc.flags", ns)
        assert result == "gcc -Wall"

    def test_braced_dotted_variable(self):
        ns = {"cc": {"cmd": "gcc"}}
        result = subst("${cc.cmd} file.c", ns)
        assert result == "gcc file.c"


class TestSubstRecursive:
    def test_recursive_expansion(self):
        ns = {
            "greeting": "hello $name",
            "name": "world",
        }
        result = subst("$greeting", ns)
        assert result == "hello world"

    def test_deeply_nested(self):
        ns = {
            "a": "$b",
            "b": "$c",
            "c": "$d",
            "d": "value",
        }
        result = subst("$a", ns)
        assert result == "value"

    def test_command_line_pattern(self):
        ns = {
            "cc": {
                "cmd": "gcc",
                "flags": "$cc.opt_flag -Wall",
                "opt_flag": "-O2",
            }
        }
        result = subst("$cc.cmd $cc.flags", ns)
        assert result == "gcc -O2 -Wall"


class TestSubstLists:
    def test_list_value(self):
        ns = {"flags": ["-Wall", "-O2", "-g"]}
        result = subst("$flags", ns)
        assert result == "-Wall -O2 -g"

    def test_list_in_context(self):
        ns = {"CC": "gcc", "FLAGS": ["-Wall", "-O2"]}
        result = subst("$CC $FLAGS -c file.c", ns)
        assert result == "gcc -Wall -O2 -c file.c"

    def test_subst_list(self):
        ns = {"cmd": "gcc -Wall"}
        result = subst_list("$cmd -c file.c", ns)
        assert result == ["gcc", "-Wall", "-c", "file.c"]


class TestSubstErrors:
    def test_missing_variable(self):
        with pytest.raises(MissingVariableError) as exc_info:
            subst("$UNDEFINED", {})
        assert "UNDEFINED" in str(exc_info.value)

    def test_circular_reference(self):
        ns = {
            "a": "$b",
            "b": "$a",
        }
        with pytest.raises(CircularReferenceError) as exc_info:
            subst("$a", ns)
        # Should contain both variables in the chain
        assert "a" in str(exc_info.value)
        assert "b" in str(exc_info.value)

    def test_self_reference(self):
        ns = {"x": "$x"}
        with pytest.raises(CircularReferenceError):
            subst("$x", ns)

    def test_longer_cycle(self):
        ns = {
            "a": "$b",
            "b": "$c",
            "c": "$a",
        }
        with pytest.raises(CircularReferenceError):
            subst("$a", ns)


class TestSubstEdgeCases:
    def test_empty_string(self):
        result = subst("", {})
        assert result == ""

    def test_only_variable(self):
        result = subst("$x", {"x": "value"})
        assert result == "value"

    def test_variable_at_end(self):
        result = subst("prefix $x", {"x": "suffix"})
        assert result == "prefix suffix"

    def test_bool_value(self):
        ns = {"flag": True, "other": False}
        result = subst("$flag $other", ns)
        assert result == "1 0"

    def test_int_value(self):
        result = subst("count is $n", {"n": 42})
        assert result == "count is 42"

    def test_variable_like_but_not(self):
        # $ at end of string
        result = subst("cost is $", {})
        assert result == "cost is $"

    def test_invalid_variable_name_chars(self):
        # $ followed by invalid char - should not substitute
        result = subst("$-foo", {})
        assert result == "$-foo"

    def test_mixed_valid_invalid(self):
        result = subst("$valid $-invalid", {"valid": "ok"})
        assert result == "ok $-invalid"


class TestEscape:
    def test_escape_dollars(self):
        assert escape("$VAR") == "$$VAR"
        assert escape("$a$b") == "$$a$$b"
        assert escape("no dollars") == "no dollars"

    def test_already_escaped(self):
        assert escape("$$VAR") == "$$$$VAR"
