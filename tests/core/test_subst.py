# SPDX-License-Identifier: MIT
"""Tests for pcons.core.subst.

The substitution system returns structured token lists, not strings.
Shell quoting happens only at the final step via to_shell_command().
"""

import pytest

from pcons.core.errors import (
    CircularReferenceError,
    MissingVariableError,
    SubstitutionError,
)
from pcons.core.subst import MultiCmd, Namespace, escape, subst, to_shell_command


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


class TestSubstBasic:
    """Test basic variable substitution."""

    def test_no_variables(self):
        # String template is auto-tokenized on whitespace
        result = subst("hello world", {})
        assert result == ["hello", "world"]

    def test_simple_variable(self):
        result = subst("hello $name", {"name": "world"})
        assert result == ["hello", "world"]

    def test_braced_variable(self):
        result = subst("hello ${name}", {"name": "world"})
        assert result == ["hello", "world"]

    def test_multiple_variables(self):
        result = subst("$a and $b", {"a": "foo", "b": "bar"})
        assert result == ["foo", "and", "bar"]

    def test_escaped_dollar(self):
        # $$ becomes literal $
        result = subst("price $$10", {})
        assert result == ["price", "$10"]

    def test_double_escape(self):
        # $$$$ = two escaped dollars = $$
        result = subst("$$$$", {})
        assert result == ["$$"]

    def test_triple_escape(self):
        # $$$$$$ = three escaped dollars = $$$
        result = subst("$$$$$$", {})
        assert result == ["$$$"]


class TestSubstListTemplate:
    """Test list-based command templates (explicit tokens)."""

    def test_list_template_no_vars(self):
        result = subst(["gcc", "-c", "file.c"], {})
        assert result == ["gcc", "-c", "file.c"]

    def test_list_template_with_vars(self):
        result = subst(
            ["$cc.cmd", "-c", "$src"], {"cc": {"cmd": "gcc"}, "src": "main.c"}
        )
        assert result == ["gcc", "-c", "main.c"]

    def test_list_template_preserves_spaces(self):
        # List elements are explicit tokens - spaces in elements are preserved
        result = subst(["echo", "hello world"], {})
        assert result == ["echo", "hello world"]


class TestSubstNamespaced:
    """Test namespaced (dotted) variable access."""

    def test_dotted_variable(self):
        ns = {"cc": {"cmd": "gcc", "flags": "-Wall"}}
        result = subst("$cc.cmd $cc.flags", ns)
        assert result == ["gcc", "-Wall"]

    def test_braced_dotted_variable(self):
        ns = {"cc": {"cmd": "gcc"}}
        result = subst("${cc.cmd} file.c", ns)
        assert result == ["gcc", "file.c"]


class TestSubstRecursive:
    """Test recursive variable expansion."""

    def test_recursive_expansion(self):
        # When a single token expands to a multi-word string, it stays as one token
        ns = {
            "greeting": "hello $name",
            "name": "world",
        }
        result = subst("$greeting", ns)
        # String template "$greeting" is a single token, expands to "hello world"
        assert result == ["hello world"]

    def test_recursive_expansion_string_template(self):
        # To get multiple tokens from recursive expansion, start with multiple tokens
        ns = {
            "greeting": "hello",
            "name": "world",
        }
        result = subst("$greeting $name", ns)
        assert result == ["hello", "world"]

    def test_deeply_nested(self):
        ns = {
            "a": "$b",
            "b": "$c",
            "c": "$d",
            "d": "value",
        }
        result = subst("$a", ns)
        assert result == ["value"]

    def test_command_line_pattern(self):
        # String variable with multiple words stays as one token
        ns = {
            "cc": {
                "cmd": "gcc",
                "flags": "$cc.opt_flag -Wall",
                "opt_flag": "-O2",
            }
        }
        result = subst("$cc.cmd $cc.flags", ns)
        # $cc.flags expands to "-O2 -Wall" as a single token
        assert result == ["gcc", "-O2 -Wall"]

    def test_command_line_with_list(self):
        # Use lists for multiple tokens
        ns = {
            "cc": {
                "cmd": "gcc",
                "flags": ["-O2", "-Wall"],
            }
        }
        result = subst("$cc.cmd $cc.flags", ns)
        # List expands to multiple tokens
        assert result == ["gcc", "-O2", "-Wall"]


class TestSubstListExpansion:
    """Test list variable expansion."""

    def test_list_variable_expands_to_multiple_tokens(self):
        ns = {"flags": ["-Wall", "-O2", "-g"]}
        result = subst("$flags", ns)
        assert result == ["-Wall", "-O2", "-g"]

    def test_list_in_context(self):
        ns = {"CC": "gcc", "FLAGS": ["-Wall", "-O2"]}
        result = subst("$CC $FLAGS -c file.c", ns)
        assert result == ["gcc", "-Wall", "-O2", "-c", "file.c"]

    def test_list_in_list_template(self):
        ns = {"cc": {"cmd": "gcc", "flags": ["-Wall", "-O2"]}}
        result = subst(["$cc.cmd", "$cc.flags", "-c", "file.c"], ns)
        assert result == ["gcc", "-Wall", "-O2", "-c", "file.c"]

    def test_list_embedded_in_token_raises(self):
        # A list variable cannot be embedded in a partial token
        ns = {"flags": ["-Wall", "-O2"]}
        with pytest.raises(SubstitutionError) as exc_info:
            subst("prefix$flags", ns)
        assert "prefix" in str(exc_info.value)


class TestSubstFunctions:
    """Test function-style syntax: ${prefix(...)}, ${suffix(...)}, etc.

    Note: Function calls with spaces in arguments must be in list templates
    since string templates are tokenized on whitespace.
    """

    def test_prefix_function(self):
        # Function calls must be in list templates (spaces break string tokenization)
        ns = {"iprefix": "-I", "includes": ["/usr/include", "/opt/local/include"]}
        result = subst(["${prefix(iprefix, includes)}"], ns)
        assert result == ["-I/usr/include", "-I/opt/local/include"]

    def test_prefix_with_dotted_vars(self):
        ns = {"cc": {"iprefix": "-I", "includes": ["src", "include"]}}
        result = subst(["${prefix(cc.iprefix, cc.includes)}"], ns)
        assert result == ["-Isrc", "-Iinclude"]

    def test_prefix_empty_list(self):
        ns = {"iprefix": "-I", "includes": []}
        result = subst(["${prefix(iprefix, includes)}"], ns)
        assert result == []

    def test_suffix_function(self):
        ns = {"files": ["main", "util"], "suffix": ".o"}
        result = subst(["${suffix(files, suffix)}"], ns)
        assert result == ["main.o", "util.o"]

    def test_wrap_function(self):
        ns = {"prefix": "-I", "dirs": ["a", "b"], "suffix": "/include"}
        result = subst(["${wrap(prefix, dirs, suffix)}"], ns)
        assert result == ["-Ia/include", "-Ib/include"]

    def test_join_function(self):
        ns = {"sep": ",", "items": ["a", "b", "c"]}
        result = subst(["${join(sep, items)}"], ns)
        assert result == ["a,b,c"]

    def test_prefix_in_command_template(self):
        ns = {
            "cc": {
                "cmd": "gcc",
                "iprefix": "-I",
                "includes": ["/usr/include"],
                "flags": ["-Wall"],
            }
        }
        result = subst(
            [
                "$cc.cmd",
                "${prefix(cc.iprefix, cc.includes)}",
                "$cc.flags",
                "-c",
                "file.c",
            ],
            ns,
        )
        assert result == ["gcc", "-I/usr/include", "-Wall", "-c", "file.c"]

    def test_unknown_function_raises(self):
        with pytest.raises(SubstitutionError) as exc_info:
            subst(["${unknown(a, b)}"], {"a": "x", "b": "y"})
        assert "unknown" in str(exc_info.value).lower()

    def test_prefix_wrong_arg_count(self):
        with pytest.raises(SubstitutionError) as exc_info:
            subst(["${prefix(a)}"], {"a": "-I"})
        assert "2 args" in str(exc_info.value)


class TestSubstErrors:
    """Test error handling."""

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
    """Test edge cases and special handling."""

    def test_empty_string(self):
        result = subst("", {})
        assert result == []

    def test_only_variable(self):
        result = subst("$x", {"x": "value"})
        assert result == ["value"]

    def test_bool_value(self):
        # Booleans are converted to Python's string representation
        ns = {"flag": True, "other": False}
        result = subst("$flag $other", ns)
        assert result == ["True", "False"]

    def test_int_value(self):
        result = subst("count $n", {"n": 42})
        assert result == ["count", "42"]

    def test_variable_like_but_not(self):
        # $ at end of string - kept as is
        result = subst("cost $", {})
        assert result == ["cost", "$"]

    def test_adjacent_to_punctuation(self):
        # Variable followed by punctuation
        result = subst("$name!", {"name": "test"})
        assert result == ["test!"]


class TestMultiCmd:
    """Test MultiCmd for multiple commands in a single build step."""

    def test_multicmd_basic(self):
        multi = MultiCmd(["mkdir -p dir", "touch dir/file"])
        result = subst(multi, {})
        assert len(result) == 2
        assert result[0] == ["mkdir", "-p", "dir"]
        assert result[1] == ["touch", "dir/file"]

    def test_multicmd_with_variables(self):
        multi = MultiCmd(["$cmd1", "$cmd2"])
        result = subst(multi, {"cmd1": "first", "cmd2": "second"})
        assert len(result) == 2
        assert result[0] == ["first"]
        assert result[1] == ["second"]

    def test_multicmd_list_templates(self):
        multi = MultiCmd([["mkdir", "-p", "$dir"], ["touch", "$dir/file"]])
        result = subst(multi, {"dir": "output"})
        assert len(result) == 2
        assert result[0] == ["mkdir", "-p", "output"]
        assert result[1] == ["touch", "output/file"]


class TestToShellCommand:
    """Test conversion to shell command string."""

    def test_simple_command(self):
        tokens = ["gcc", "-c", "file.c"]
        result = to_shell_command(tokens)
        assert result == "gcc -c file.c"

    def test_quoting_spaces(self):
        tokens = ["echo", "hello world"]
        result = to_shell_command(tokens, shell="bash")
        assert result == "echo 'hello world'"

    def test_quoting_special_chars(self):
        tokens = ["echo", "it's"]
        result = to_shell_command(tokens, shell="bash")
        # Single quote in string needs double quotes
        assert result == 'echo "it\'s"'

    def test_multicmd_join(self):
        # Multiple commands (from MultiCmd expansion)
        tokens = [["mkdir", "-p", "dir"], ["touch", "dir/file"]]
        result = to_shell_command(tokens)
        assert result == "mkdir -p dir && touch dir/file"

    def test_multicmd_custom_join(self):
        tokens = [["cmd1"], ["cmd2"]]
        result = to_shell_command(tokens, multi_join=" ; ")
        assert result == "cmd1 ; cmd2"

    def test_empty_token_quoted(self):
        tokens = ["echo", ""]
        result = to_shell_command(tokens, shell="bash")
        assert "''" in result

    def test_shell_powershell(self):
        tokens = ["echo", "hello world"]
        result = to_shell_command(tokens, shell="powershell")
        assert result == "echo 'hello world'"

    def test_shell_cmd(self):
        tokens = ["echo", "hello world"]
        result = to_shell_command(tokens, shell="cmd")
        assert result == 'echo "hello world"'


class TestGeneratorVariables:
    """Test $$ escaping for generator variables like $SOURCE, $TARGET."""

    def test_dollar_dollar_becomes_dollar(self):
        # $$TARGET becomes $TARGET (which generators then convert to native syntax)
        result = subst("-o $$TARGET $$SOURCE", {})
        assert result == ["-o", "$TARGET", "$SOURCE"]

    def test_in_list_template(self):
        result = subst(["-o", "$$TARGET", "$$SOURCE"], {})
        assert result == ["-o", "$TARGET", "$SOURCE"]

    def test_full_command_template(self):
        ns = {"cc": {"cmd": "gcc", "flags": ["-Wall"]}}
        result = subst(["$cc.cmd", "$cc.flags", "-c", "-o", "$$TARGET", "$$SOURCE"], ns)
        assert result == ["gcc", "-Wall", "-c", "-o", "$TARGET", "$SOURCE"]


class TestRealWorldPatterns:
    """Test patterns from actual toolchain usage."""

    def test_gcc_compile_command(self):
        ns = {
            "cc": {
                "cmd": "gcc",
                "flags": ["-Wall", "-O2"],
                "iprefix": "-I",
                "includes": ["/usr/include", "src"],
                "dprefix": "-D",
                "defines": ["DEBUG", "VERSION=1"],
                "depflags": ["-MD", "-MF", "$$TARGET.d"],
            }
        }
        result = subst(
            [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c",
                "-o",
                "$$TARGET",
                "$$SOURCE",
            ],
            ns,
        )
        assert result == [
            "gcc",
            "-Wall",
            "-O2",
            "-I/usr/include",
            "-Isrc",
            "-DDEBUG",
            "-DVERSION=1",
            "-MD",
            "-MF",
            "$TARGET.d",
            "-c",
            "-o",
            "$TARGET",
            "$SOURCE",
        ]

    def test_linker_command(self):
        ns = {
            "link": {
                "cmd": "gcc",
                "flags": [],
                "Lprefix": "-L",
                "libdirs": ["/usr/lib"],
                "lprefix": "-l",
                "libs": ["m", "pthread"],
            }
        }
        result = subst(
            [
                "$link.cmd",
                "$link.flags",
                "-o",
                "$$TARGET",
                "$$SOURCES",
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
            ns,
        )
        assert result == [
            "gcc",
            "-o",
            "$TARGET",
            "$SOURCES",
            "-L/usr/lib",
            "-lm",
            "-lpthread",
        ]

    def test_msvc_compile_command(self):
        ns = {
            "cc": {
                "cmd": "cl.exe",
                "flags": ["/nologo"],
                "iprefix": "/I",
                "includes": ["src", "include"],
                "dprefix": "/D",
                "defines": ["WIN32"],
            }
        }
        result = subst(
            [
                "$cc.cmd",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "/c",
                "/Fo$$TARGET",
                "$$SOURCE",
            ],
            ns,
        )
        assert result == [
            "cl.exe",
            "/nologo",
            "/Isrc",
            "/Iinclude",
            "/DWIN32",
            "/c",
            "/Fo$TARGET",
            "$SOURCE",
        ]


class TestEscape:
    """Test the escape() helper function."""

    def test_escape_dollars(self):
        assert escape("$VAR") == "$$VAR"
        assert escape("$a$b") == "$$a$$b"
        assert escape("no dollars") == "no dollars"

    def test_already_escaped(self):
        assert escape("$$VAR") == "$$$$VAR"
