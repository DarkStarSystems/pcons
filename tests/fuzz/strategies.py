# SPDX-License-Identifier: MIT
"""Hypothesis strategies shared by the property tests.

The vocabularies here are deliberately small and realistic: property
testing finds bugs by hitting awkward *combinations*, not by inventing
exotic tokens, so a handful of flags that collide in interesting ways
beats random text.
"""

from __future__ import annotations

import platform
import re

from hypothesis import strategies as st

from pcons.core.flags import Flag, FlagPair
from pcons.core.subst import _SHELL_OPERATORS

# Flags whose argument is a separate token, as a toolchain would report them.
SEPARATED_ARG_FLAGS: frozenset[str] = frozenset({"-I", "-F", "-framework", "-isystem"})

# Driver pass-through flags: consecutive ones form a single directive, so
# they are never de-duplicated.
PASSTHROUGH_FLAGS: frozenset[str] = frozenset({"-Xlinker"})

# Flags that carry their argument themselves (or take none).
SIMPLE_FLAGS = ["-O2", "-Wall", "-g", "-DFOO=1", "-L/usr/lib"]

# Arguments. Some look like flags, which is where pair tracking goes wrong.
ARGUMENTS = ["inc", "sub dir", "-Wall", "-I"]

# A flag used inside a FlagPair may or may not be one the toolchain knows
# takes a separate argument -- FlagPair exists precisely to mark a pair
# atomic when the toolchain doesn't know about it.
PAIR_FLAGS = ["-I", "-framework", "-custom"]


def flag_tokens(*, pairs: bool = True) -> st.SearchStrategy[str | FlagPair]:
    """Single flag-list entries: plain tokens, and optionally FlagPairs."""
    plain = st.sampled_from(
        [
            *SIMPLE_FLAGS,
            *sorted(SEPARATED_ARG_FLAGS),
            *sorted(PASSTHROUGH_FLAGS),
            *ARGUMENTS,
        ]
    )
    if not pairs:
        return plain
    return st.one_of(
        plain,
        st.builds(FlagPair, st.sampled_from(PAIR_FLAGS), st.sampled_from(ARGUMENTS)),
    )


def _well_formed(flags: list) -> list:
    """Drop entries that leave the flat token stream ambiguous.

    A flag that claims the next token -- a separated-argument flag like
    ``-I``, or a pass-through like ``-Xlinker`` -- is recognized by
    position once the list is flat. That reading survives a round trip
    only if every such flag is followed by an ordinary argument: not by
    another flag that claims the next token, not by end-of-list, and not
    by a group whose first token it would swallow. Properties that
    re-read an emitted list assume this; the structural properties do
    not, and keep fuzzing the ragged cases.
    """
    claims_next = SEPARATED_ARG_FLAGS | PASSTHROUGH_FLAGS
    kept: list = []  # built right to left, so "what follows" is already known
    for flag in reversed(flags):
        if isinstance(flag, Flag):
            if flag.argument in claims_next:
                continue  # the pair would re-read as two flags
            kept.append(flag)
            continue
        if flag in claims_next:
            following = kept[-1] if kept else None
            if (
                following is None
                or isinstance(following, Flag)
                or following in claims_next
            ):
                continue  # no ordinary argument for it to claim
        kept.append(flag)
    kept.reverse()
    return kept


def flag_lists(
    *, pairs: bool = True, well_formed: bool = False, max_size: int = 10
) -> st.SearchStrategy[list]:
    """Flag lists as they reach deduplicate_flags()/merge_flags()."""
    lists = st.lists(flag_tokens(pairs=pairs), max_size=max_size)
    return lists.map(_well_formed) if well_formed else lists


# Characters that are legal in a filename and awkward in a build file:
# ninja escapes some, the shell others, and Windows forbids a third set.
#
# Two are left out on purpose rather than by oversight. Backslash: pcons
# reads it as a path separator and normalizes it to "/", by design. Pipe:
# ninja gives it no escape and reads it as the implicit-dependency
# separator even in the middle of a path, so such a filename cannot be
# written in a ninja file at all.
NAME_CHARS = "ab $#'()&;+=@%!,~^[]{}éあ"
if platform.system() != "Windows":
    NAME_CHARS += ':"*?<>'

_name_parts = st.text(alphabet=NAME_CHARS, max_size=5)


# Everything a shell might read as syntax, plus ordinary characters so
# tokens are not uniformly hostile.
SHELL_CHARS = "ab -_/. \t\n\"'\\$`!*?[](){}|&;<>#~=,:"

# Variables the ninja generator defines: a token that is exactly one of
# these is a reference the generator must leave alone, not a literal.
_NINJA_VAR = re.compile(r"\$(in|out|topdir|out_basename|source_\d+|target_\d+)")


def shell_tokens(
    *, max_size: int = 8, allow_empty: bool = True
) -> st.SearchStrategy[str]:
    """Single command-line tokens, as a build script might supply them.

    Three kinds are left out, all because they are meant to reach the
    command line unquoted: a reference to a variable the generator
    defines, a bare shell operator, and a token starting with "~", which
    the shell expands to a home directory -- pcons leaves that alone, so
    a build script can write "~/foo" and mean it. The first two have
    their own tests.
    """
    return st.text(
        alphabet=SHELL_CHARS, min_size=0 if allow_empty else 1, max_size=max_size
    ).filter(
        lambda token: (
            not _NINJA_VAR.match(token)
            and token not in _SHELL_OPERATORS
            and not token.startswith("~")
        )
    )


@st.composite
def variable_chains(draw, *, max_vars: int = 5) -> tuple[dict, bool, str | None]:
    """A namespace of variables that reference each other in a chain.

    Returns (namespace, is_cyclic, terminal_value): each `v<i>` holds
    either a reference to another variable or a literal, so expanding
    `$v0` either walks to a literal or comes back to a variable it has
    already passed through.
    """
    count = draw(st.integers(min_value=1, max_value=max_vars))
    links = draw(
        st.lists(
            st.integers(min_value=-1, max_value=count - 1),
            min_size=count,
            max_size=count,
        )
    )
    values = {
        f"v{i}": f"$v{target}" if target >= 0 else f"literal{i}"
        for i, target in enumerate(links)
    }

    seen: set[int] = set()
    node = 0
    while True:
        if node in seen:
            return values, True, None
        seen.add(node)
        if links[node] < 0:
            return values, False, f"literal{node}"
        node = links[node]


@st.composite
def path_projects(draw, *, max_files: int = 3) -> tuple[str | None, list]:
    """A little project as (source subdirectory, [(source, output), ...]).

    Names are prefixed and suffixed with ordinary characters, so they
    never start with a dash or end in the space or dot that Windows
    rejects, and the index keeps them distinct on case-insensitive and
    Unicode-normalizing filesystems.
    """
    count = draw(st.integers(min_value=1, max_value=max_files))
    files = [
        (f"s{i}{draw(_name_parts)}.txt", f"o{i}{draw(_name_parts)}.txt")
        for i in range(count)
    ]
    subdir = draw(st.one_of(st.none(), _name_parts.map(lambda part: f"d{part}x")))
    return subdir, files


@st.composite
def dependency_graphs(draw, *, max_targets: int = 8, acyclic: bool) -> tuple[int, list]:
    """A dependency graph as (target_count, edges).

    An edge ``(a, b)`` means "target a depends on target b". Acyclic
    graphs only ever point at a lower index, which makes them DAGs by
    construction; cyclic-allowed graphs may point anywhere but never at
    themselves.
    """
    count = draw(st.integers(min_value=1, max_value=max_targets))
    candidates = st.tuples(
        st.integers(min_value=0, max_value=count - 1),
        st.integers(min_value=0, max_value=count - 1),
    )
    edges = draw(st.lists(candidates, max_size=2 * count))
    keep = (lambda a, b: a > b) if acyclic else (lambda a, b: a != b)
    return count, sorted({(a, b) for a, b in edges if keep(a, b)})


def has_cycle(count: int, edges: list[tuple[int, int]]) -> bool:
    """Reference cycle check -- deliberately naive, so it is obviously right."""
    successors: dict[int, list[int]] = {i: [] for i in range(count)}
    for a, b in edges:
        successors[a].append(b)

    visiting: set[int] = set()
    done: set[int] = set()

    def visit(node: int) -> bool:
        if node in visiting:
            return True
        if node in done:
            return False
        visiting.add(node)
        found = any(visit(nxt) for nxt in successors[node])
        visiting.discard(node)
        done.add(node)
        return found

    return any(visit(node) for node in range(count))
