# SPDX-License-Identifier: MIT
"""Property tests for flag de-duplication and merging.

Flag lists are built by combining usage requirements from many targets,
so the same list is merged into repeatedly. The properties that matter
are structural: nothing is invented, nothing is reordered, a flag is
never separated from its argument, and merging what is already there
changes nothing.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pcons.core.flags import (
    Flag,
    FlagList,
    dedupe_groups,
    deduplicate_flags,
    flatten_flags,
    merge_flags,
    parse_flags,
)

from .strategies import (
    PASSTHROUGH_FLAGS,
    SEPARATED_ARG_FLAGS,
    flag_list_mutations,
    flag_lists,
)

pytestmark = pytest.mark.fuzz

SEP = SEPARATED_ARG_FLAGS
PASS = PASSTHROUGH_FLAGS


def expand(flags):
    """The flat token list a flag list stands for (groups flattened)."""
    return flatten_flags(Flag((f,)) if not isinstance(f, Flag) else f for f in flags)


def is_subsequence(sub, seq):
    """Whether *sub* appears in *seq* in order (gaps allowed)."""
    remaining = iter(seq)
    return all(item in remaining for item in sub)


@given(flag_lists())
def test_dedup_returns_plain_strings(flags):
    """FlagPairs are expanded; callers downstream only ever see strings."""
    assert all(isinstance(flag, str) for flag in deduplicate_flags(flags, SEP, PASS))


@given(flag_lists())
def test_dedup_only_drops(flags):
    """De-duplication removes tokens; it never adds or reorders them."""
    result = deduplicate_flags(flags, SEP, PASS)
    assert is_subsequence(result, expand(flags))


@given(flag_lists())
def test_dedup_keeps_every_distinct_token(flags):
    """Dropping a duplicate must never drop the last copy of a token."""
    result = deduplicate_flags(flags, SEP, PASS)
    assert set(result) == set(expand(flags))


@given(flag_lists(well_formed=True))
def test_dedup_keeps_separated_args_attached(flags):
    """Dropping duplicates never orphans a flag from its argument.

    Asked of the groups, not the tokens: a token that looks like a flag
    may be another flag's argument (``-Xlinker -F`` hands ``-F`` to the
    linker), and only the grouping knows the difference.
    """
    kept = dedupe_groups(parse_flags(flags, SEP, PASS))
    for group in kept:
        if group.tokens[0] in SEP:
            assert len(group.tokens) >= 2, f"{group.tokens[0]} lost its argument"


@given(flag_lists(pairs=False, well_formed=True))
def test_grouping_survives_a_round_trip_through_tokens(flags):
    """Groups written out as tokens read back as the same groups.

    True for the flags the rules describe, which is what lets a flat list
    from outside (a .pc file, a build script) be grouped once and trusted
    afterwards.

    Not true for an explicit group the rules cannot reproduce --
    ``FlagPair("-custom", "value")`` flattens to two tokens that read as
    two flags, and nothing in them says otherwise. That is exactly why a
    FlagList keeps its groups instead of re-deriving them from tokens:
    the round trip this property describes is the one thing the design
    never relies on.
    """
    groups = parse_flags(flags, SEP, PASS)
    assert parse_flags(flatten_flags(groups), SEP, PASS) == groups


@given(flag_lists(pairs=False))
def test_dedup_is_idempotent(flags):
    """De-duplicating an already de-duplicated list is a no-op.

    Plain tokens only: expanding a FlagPair to two strings loses the fact
    that they belong together, so a second pass over the *expanded* list
    cannot be expected to reconstruct it.
    """
    once = deduplicate_flags(flags, SEP, PASS)
    assert deduplicate_flags(once, SEP, PASS) == once


@given(flag_lists())
def test_merge_into_empty_matches_dedup(flags):
    """The two entry points agree on what a duplicate is."""
    merged = FlagList(separated=SEP, passthrough=PASS)
    merge_flags(merged, flags, SEP, PASS)
    assert merged == deduplicate_flags(flags, SEP, PASS)


@given(flag_lists(), flag_lists())
def test_merge_only_appends(existing, new):
    """Merging never disturbs flags that were already there."""
    merged = FlagList(existing, separated=SEP, passthrough=PASS)
    before = list(merged)
    merge_flags(merged, new, SEP, PASS)
    assert merged[: len(before)] == before


@given(flag_lists(), flag_lists())
def test_merge_is_idempotent(existing, new):
    """Merging the same requirements twice must not grow the flag list.

    Usage requirements propagate through the target graph and the same
    ones commonly arrive by several paths; if a repeat merge appended,
    flag lists would grow with every edge.

    No well-formedness precondition: a FlagList settles its grouping on
    the way in and never re-derives it, so even a ragged flag list merges
    idempotently.
    """
    merged = FlagList(existing, separated=SEP, passthrough=PASS)
    merge_flags(merged, new, SEP, PASS)
    once = list(merged)
    merge_flags(merged, new, SEP, PASS)
    assert merged == once


@given(flag_lists(), st.lists(flag_list_mutations(), max_size=8))
def test_a_flag_list_always_agrees_with_its_groups(initial, mutations):
    """However a FlagList is edited, its tokens are its groups' tokens.

    A FlagList maintains two views of the same thing, and every list
    mutator has to keep them in step -- one overridden method forgotten
    and the tokens quietly stop matching the grouping that merging works
    from. So: apply arbitrary edits, and check after every one.
    """
    flags = FlagList(initial, separated=SEP, passthrough=PASS)
    assert list(flags) == flatten_flags(flags.groups)

    for mutate in mutations:
        mutate(flags)
        assert list(flags) == flatten_flags(flags.groups), f"desynced after {mutate}"

    copy = flags.copy()
    assert list(copy) == list(flags)
    assert copy.groups == flags.groups


@given(flag_lists(), flag_lists())
def test_merge_adds_every_new_token(existing, new):
    """Everything in *new* is present after the merge, one way or another."""
    merged = FlagList(existing, separated=SEP, passthrough=PASS)
    merge_flags(merged, new, SEP, PASS)
    assert set(expand(new)) <= set(merged)
