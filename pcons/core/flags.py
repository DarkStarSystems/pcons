# SPDX-License-Identifier: MIT
"""Flags, and the groups they come in.

A flag is not always one token. ``-I`` takes the next token as its
argument, ``-Xlinker -rpath`` is one directive to the linker, and a
toolchain may know pairings that pcons does not. Treating such a pair as
two independent tokens is what breaks de-duplication: the flag gets
dropped and its argument left behind, or a repeat is not recognized and
the list grows on every merge.

So flags are grouped, once, where they enter: :func:`parse_flags` turns a
flat token list into :class:`Flag` groups using the toolchain's rules,
and everything after that -- merging, de-duplication, comparison -- works
on whole groups, which need no rules at all. Only the very last step,
handing tokens to a command line, flattens them again.
"""

from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from typing import Any, SupportsIndex

    from pcons.core.subst import FlagToken


@dataclass(frozen=True, eq=False)
class Flag:
    """One flag and the tokens that belong with it.

    Two flags are the same flag when their tokens are, so a group is its
    own de-duplication key -- there is nothing positional left to get
    wrong. That is why a group has to span everything that means one
    thing: a ``-Xlinker -rpath -Xlinker /p`` run is a single directive to
    the linker, and de-duplicating any part of it separately would leave
    the rest saying something else.
    """

    tokens: tuple[FlagToken, ...]

    def __iter__(self) -> Iterator[FlagToken]:
        """Iterate the tokens, so a group unpacks like the pair it usually is."""
        return iter(self.tokens)

    def __eq__(self, other: object) -> bool:
        """A flag is its tokens; how it was spelled is not part of it.

        So a FlagPair equals the plain Flag holding the same two tokens.
        Generated dataclass equality would call them different for having
        different classes, and a pair would stop matching itself the
        moment it made a round trip through a flat token list.
        """
        if isinstance(other, Flag):
            return self.tokens == other.tokens
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.tokens)

    def __str__(self) -> str:
        return " ".join(str(token) for token in self.tokens)


class FlagPair(Flag):
    """A flag and its argument, kept together through merging.

    The two-token :class:`Flag`, spelled the way it reads. Marks a pair
    as atomic even when the toolchain doesn't list the flag as taking a
    separate argument.

    Example:
        env.cxx.flags.append(FlagPair("-custom-flag", "value"))
    """

    def __init__(self, flag: str, argument: FlagToken) -> None:
        super().__init__((flag, argument))

    @property
    def flag(self) -> str:
        return str(self.tokens[0])

    @property
    def argument(self) -> FlagToken:
        return self.tokens[1]


# Default when no toolchain provides a flag set.
DEFAULT_SEPARATED_ARG_FLAGS: frozenset[str] = frozenset()


def is_separated_arg_flag(
    flag: FlagToken, separated_arg_flags: frozenset[str] | None = None
) -> bool:
    """Check if a flag takes its argument as a separate token."""
    return flag in (separated_arg_flags or DEFAULT_SEPARATED_ARG_FLAGS)


def parse_flags(
    flags: Sequence[FlagToken | Flag],
    separated_arg_flags: frozenset[str] | None = None,
    passthrough_flags: frozenset[str] | None = None,
) -> list[Flag]:
    """Group a flat flag list, left to right.

    A flag in *separated_arg_flags* claims the token after it. A flag in
    *passthrough_flags* (``-Xlinker``) does too, and the whole
    consecutive run of them becomes one group: ``-Xlinker -rpath
    -Xlinker /p`` is a single directive to the linker, so the run is the
    smallest thing that still means what was written.

    Anything already grouped is passed through untouched. This is the
    only place the positional rules apply, so grouping cannot drift from
    one call to the next.
    """
    separated = separated_arg_flags or DEFAULT_SEPARATED_ARG_FLAGS
    passthrough = passthrough_flags or frozenset()

    def pair_at(
        index: int, names: frozenset[str]
    ) -> tuple[FlagToken, FlagToken] | None:
        """The flag/argument pair starting at *index*, if there is one.

        A group already formed cannot be split, so a flag in front of one
        has no argument of its own.
        """
        if index + 1 >= len(flags):
            return None
        flag, argument = flags[index], flags[index + 1]
        if isinstance(flag, Flag) or flag not in names:
            return None
        if isinstance(argument, Flag):
            return None
        return flag, argument

    groups: list[Flag] = []
    i = 0
    while i < len(flags):
        item = flags[i]
        if isinstance(item, Flag):
            groups.append(item)
            i += 1
            continue

        run: list[FlagToken] = []
        while (pair := pair_at(i, passthrough)) is not None:
            run.extend(pair)
            i += 2
        if run:
            groups.append(Flag(tuple(run)))
            continue

        pair = pair_at(i, separated)
        if pair is not None:
            groups.append(Flag(pair))
            i += 2
            continue

        groups.append(Flag((item,)))
        i += 1

    return groups


def flatten_flags(groups: Iterable[Flag]) -> list[FlagToken]:
    """The tokens these groups put on a command line, in order."""
    return [token for group in groups for token in group.tokens]


def dedupe_groups(groups: Iterable[Flag]) -> list[Flag]:
    """Drop repeated groups, first occurrence wins, order preserved."""
    seen: set[tuple[FlagToken, ...]] = set()
    result: list[Flag] = []
    for group in groups:
        if group.tokens not in seen:
            seen.add(group.tokens)
            result.append(group)
    return result


def deduplicate_flags(
    flags: Sequence[FlagToken | Flag],
    separated_arg_flags: frozenset[str] | None = None,
    passthrough_flags: frozenset[str] | None = None,
) -> list[FlagToken]:
    """De-duplicate a flag list, first occurrence wins, order preserved.

    Flags in *separated_arg_flags* and :class:`Flag` groups de-duplicate
    as units; other flags de-duplicate as single tokens. A run of
    pass-through flags (e.g. ``-Xlinker``) de-duplicates as one unit too:
    consecutive ones form a single directive (``-Xlinker -rpath -Xlinker
    /p`` is ``-rpath /p`` to the linker), so comparing any part of it
    separately would drop a repeated ``-Xlinker -rpath`` and orphan the
    path that followed.

    Examples:
        >>> gcc_flags = frozenset(["-F", "-framework", "-I"])
        >>> deduplicate_flags(["-O2", "-Wall", "-O2"], gcc_flags)
        ['-O2', '-Wall']

        >>> deduplicate_flags(["-F", "path1", "-F", "path2"], gcc_flags)
        ['-F', 'path1', '-F', 'path2']
    """
    groups = parse_flags(flags, separated_arg_flags, passthrough_flags)
    return flatten_flags(dedupe_groups(groups))


def merge_flags(
    existing: MutableSequence[FlagToken],
    new: Sequence[FlagToken | Flag],
    separated_arg_flags: frozenset[str] | None = None,
    passthrough_flags: frozenset[str] | None = None,
) -> None:
    """Merge new flags into *existing* in place, skipping duplicates.

    Separated-argument flags and :class:`Flag` groups compare as whole
    groups. Merging the same flags again adds nothing, however many
    dependency paths they arrive by.

    Examples:
        >>> gcc_flags = frozenset(["-F"])
        >>> existing = ["-O2", "-F", "path1"]
        >>> merge_flags(existing, ["-Wall", "-F", "path1", "-F", "path2"], gcc_flags)
        >>> existing
        ['-O2', '-F', 'path1', '-Wall', '-F', 'path2']
    """
    if not new:
        return

    if isinstance(existing, FlagList):
        existing.merge(new)
        return

    present = parse_flags(list(existing), separated_arg_flags, passthrough_flags)
    added = dedupe_groups(parse_flags(new, separated_arg_flags, passthrough_flags))
    known = {group.tokens for group in present}
    for group in added:
        if group.tokens not in known:
            known.add(group.tokens)
            existing.extend(group.tokens)


class FlagList(list):
    """A tool's flags: reads as a list of tokens, remembers the groups.

    A real ``list`` of the tokens that reach the command line, so
    indexing, iteration, ``len``, ``in`` and every ``isinstance(x, list)``
    in the substitution engine behave exactly as they did --
    ``env.cc.flags[0]`` is ``"-O2"``, not a group. The grouping is there
    when it is wanted: :attr:`groups`, and :meth:`merge`, which uses it.

    The groups are what this class actually maintains; the list contents
    are their tokens, kept in step. Grouping is settled on the way in,
    from the toolchain's rules, rather than re-derived by every later
    reader -- that re-derivation is what used to lose pairs. Editing by
    token position (``flags[2] = ...``, ``del flags[1]``) can only be
    read positionally, so it re-groups from the rules and an explicit
    grouping around the edit is not preserved.
    """

    def __init__(
        self,
        items: Iterable[FlagToken | Flag] = (),
        *,
        separated: frozenset[str] = frozenset(),
        passthrough: frozenset[str] = frozenset(),
    ) -> None:
        self.separated = separated
        self.passthrough = passthrough
        self._groups: list[Flag] = parse_flags(list(items), separated, passthrough)
        super().__init__(flatten_flags(self._groups))

    @property
    def groups(self) -> tuple[Flag, ...]:
        """The flag groups, each a flag with whatever belongs to it."""
        return tuple(self._groups)

    def _sync(self) -> None:
        """Rewrite the token list from the groups."""
        super().__init__()
        super().extend(flatten_flags(self._groups))

    def _regroup(self) -> None:
        """Re-derive the groups from the current tokens, positionally."""
        self._groups = parse_flags(list(self), self.separated, self.passthrough)

    def _is_open(self, group: Flag) -> bool:
        """Whether *group* is a lone flag still waiting for its argument."""
        if len(group.tokens) != 1:
            return False
        flag = group.tokens[0]
        return flag in self.separated or flag in self.passthrough

    # -- appending keeps grouping ------------------------------------------

    def append(self, value: FlagToken | Flag) -> None:
        """Append a token or a whole group.

        A bare token that follows a flag still waiting for its argument
        joins it, so appending ``"-I"`` and then ``"inc"`` groups the
        same way passing them together would.
        """
        if isinstance(value, Flag):
            self._groups.append(value)
        elif self._groups and self._is_open(self._groups[-1]):
            flag = self._groups.pop().tokens[0]
            self._groups.append(Flag((flag, value)))
        else:
            self._groups.append(Flag((value,)))
        self._sync()

    def extend(self, values: Iterable[FlagToken | Flag]) -> None:
        """Append a whole flag list, grouping it as it goes in."""
        self._groups.extend(parse_flags(list(values), self.separated, self.passthrough))
        self._sync()

    def merge(self, values: Iterable[FlagToken | Flag]) -> None:
        """Append the groups in *values* that are not here already."""
        known = {group.tokens for group in self._groups}
        for group in parse_flags(list(values), self.separated, self.passthrough):
            if group.tokens in known:
                continue
            known.add(group.tokens)
            self._groups.append(group)
        self._sync()

    def copy(self) -> FlagList:
        """A copy that keeps the grouping (unlike ``list(flags)``)."""
        clone = FlagList(separated=self.separated, passthrough=self.passthrough)
        clone._groups = list(self._groups)
        clone._sync()
        return clone

    # -- positional edits fall back on the rules ---------------------------

    def __setitem__(self, index: Any, value: Any) -> None:
        super().__setitem__(index, value)
        self._regroup()

    def __delitem__(self, index: Any) -> None:
        super().__delitem__(index)
        self._regroup()

    def __iadd__(self, values: Iterable[FlagToken | Flag]) -> FlagList:
        self.extend(values)
        return self

    def insert(self, index: SupportsIndex, value: FlagToken | Flag) -> None:
        super().insert(index, value)
        self._regroup()

    def remove(self, value: Any) -> None:
        super().remove(value)
        self._regroup()

    def pop(self, index: SupportsIndex = -1) -> Any:
        value = super().pop(index)
        self._regroup()
        return value

    def clear(self) -> None:
        super().clear()
        self._groups = []

    def sort(self, **kwargs: Any) -> None:
        super().sort(**kwargs)
        self._regroup()

    def reverse(self) -> None:
        super().reverse()
        self._regroup()

    def __repr__(self) -> str:
        return f"FlagList({list(self)!r})"


def _union_from_toolchains(toolchains: Iterable[Any], getter: str) -> frozenset[str]:
    all_flags: set[str] = set()
    for toolchain in toolchains:
        method = getattr(toolchain, getter, None)
        if method is not None:
            flags = method()
            if flags:
                all_flags.update(flags)
    return frozenset(all_flags)


def get_separated_arg_flags_from_toolchains(
    toolchains: Iterable[Any],
) -> frozenset[str]:
    """Return the union of all toolchains' separated-argument flags."""
    return _union_from_toolchains(toolchains, "get_separated_arg_flags")


def get_passthrough_flags_from_toolchains(
    toolchains: Iterable[Any],
) -> frozenset[str]:
    """Return the union of all toolchains' pass-through flags.

    These must travel with the separated-argument set wherever flags are
    grouped: a ``-Xlinker -rpath`` pair that is not marked pass-through
    de-duplicates like any other, and the repeat it collapses leaves the
    following path with no directive in front of it.
    """
    return _union_from_toolchains(toolchains, "get_passthrough_flags")
