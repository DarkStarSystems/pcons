# SPDX-License-Identifier: MIT
"""Property tests for variable substitution and shell quoting.

`subst` is a small language -- variable references, function calls,
escaped dollars, list expansion -- and `to_shell_command` then has to get
its output past one or two more layers of parsing (ninja's, then the
shell's) with every character intact. Both are checked here by round
trip: quote a token, put it back through the parser it was quoted for,
and require the original.
"""

from __future__ import annotations

import platform
import subprocess

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pcons.core.errors import CircularReferenceError, PconsError
from pcons.core.subst import subst, to_shell_command

from .strategies import shell_tokens, variable_chains

pytestmark = pytest.mark.fuzz

posix_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="round trip models /bin/sh; cmd.exe quoting is a different model",
)


def shell_words(command_line: str) -> list[str]:
    """The words /bin/sh splits a command line into.

    The shell itself is the oracle: Python's shlex is close but not the
    same parser (it leaves the backslash in `\\``, for one), and it is the
    real shell that runs these commands.
    """
    if not command_line:
        return []  # printf with no operands still prints its format once
    printf = subprocess.run(
        ["/bin/sh", "-c", f"printf '%s\\0' {command_line}"],
        capture_output=True,
        check=True,
    )
    return printf.stdout.decode().split("\0")[:-1]


def ninja_expand(text: str) -> str:
    """What ninja hands to the shell, given a command line from a build file.

    Raises on a `$` that is neither an escape nor a variable reference --
    ninja rejects the file outright in that case.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "$":
            following = text[i + 1 : i + 2]
            assert following in (" ", ":", "$"), f"bad $-escape in {text!r}"
            out.append(following)
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


@posix_only
@given(st.lists(shell_tokens(), max_size=6))
def test_bash_quoting_round_trips(tokens):
    """A quoted command line splits back into exactly the tokens it was built from."""
    assert shell_words(to_shell_command(tokens, shell="bash")) == tokens


@posix_only
@given(st.lists(shell_tokens(allow_empty=False), max_size=6))
def test_ninja_quoting_survives_both_layers(tokens):
    """Tokens survive ninja's expansion and then the shell's word splitting.

    A command in a build file is parsed twice, so a literal dollar has to
    be escaped for both. Getting one layer right and the other wrong is
    invisible until a build runs.

    Empty tokens are excluded: for ninja they are dropped rather than
    passed as an empty argument, so that an unset variable in a command
    template leaves nothing behind instead of an empty word.
    """
    command = to_shell_command(tokens, shell="ninja")
    assert shell_words(ninja_expand(command)) == tokens


@given(st.text(alphabet="ab-_/.$", max_size=12))
def test_escaped_dollars_expand_to_literal_dollars(text):
    """`$$` means one literal `$`, whatever surrounds it."""
    template = text.replace("$", "$$")
    assert subst(template, {}) == ([text] if text else [])


@given(
    st.text(alphabet="ab $}{()_,.", max_size=12),
    st.dictionaries(
        st.text(alphabet="xy", min_size=1, max_size=2),
        st.text(alphabet="ab", max_size=3),
        max_size=3,
    ),
)
def test_bad_templates_raise_pcons_errors(template, namespace):
    """Substitution fails with a pcons error, never a raw Python exception.

    Templates reach `subst` from build scripts, so a malformed one is a
    user error and has to arrive as a message, not a traceback.
    """
    try:
        subst(template, namespace)
    except PconsError:
        pass  # A diagnosable failure is the contract.


@given(variable_chains())
def test_variable_chains_expand_or_report_a_cycle(chain):
    """Following `$a -> $b -> $c` yields the value at the end, or raises.

    Cycle detection has to fire exactly when the chain closes: too eager
    and a legal build script is rejected, too lax and pcons hangs or
    overflows the stack.
    """
    values, cyclic, terminal = chain
    if cyclic:
        with pytest.raises(CircularReferenceError):
            subst("$v0", values)
    else:
        assert subst("$v0", values) == [terminal]


@given(
    st.lists(shell_tokens(allow_empty=False), max_size=3),
    st.sampled_from([">", ">>", "|", "&&", "2>&1"]),
    st.lists(shell_tokens(allow_empty=False), max_size=3),
)
def test_shell_operators_are_never_quoted(before, operator, after):
    """An operator stays an operator; quoting one would silently change a command."""
    command = to_shell_command([*before, operator, *after], shell="bash")
    assert f" {operator} " in f" {command} "


def test_ninja_variable_references_are_left_alone():
    """The generator's own variables reach the build file unquoted."""
    assert to_shell_command(["$in", "$out"], shell="ninja") == "$in $out"
    # Any other dollar is a literal for the command. On POSIX it has two
    # layers to survive -- ninja's expansion ($$ -> $) and then the
    # shell's (\$ -> $) -- where cmd.exe leaves dollars alone, so ninja's
    # own doubling is the whole job.
    literal = to_shell_command(["$nope"], shell="ninja")
    assert literal == ('"$$nope"' if platform.system() == "Windows" else '"\\$$nope"')
