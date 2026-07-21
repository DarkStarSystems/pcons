# SPDX-License-Identifier: MIT
"""Flag de-duplication utilities.

Flags like -I or -framework may take their argument as a separate token, so
de-duplication must treat the flag+argument pair as a unit. Which flags do so
is toolchain-specific; the functions here take the flag set as a parameter.
"""

from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from typing import Any

    from pcons.core.subst import PathToken


@dataclass(frozen=True)
class FlagPair:
    """A flag and its argument, kept together during deduplication.

    Marks a pair as atomic even if the flag isn't in the toolchain's
    SEPARATED_ARG_FLAGS list.

    Example:
        env.cxx.flags.append(FlagPair("-custom-flag", "value"))
    """

    flag: str
    argument: str

    def __iter__(self) -> Iterator[str]:
        """Allow unpacking and iteration: flag, arg = FlagPair(...)"""
        return iter([self.flag, self.argument])


# Default when no toolchain provides a flag set.
DEFAULT_SEPARATED_ARG_FLAGS: frozenset[str] = frozenset()


def is_separated_arg_flag(
    flag: str | PathToken, separated_arg_flags: frozenset[str] | None = None
) -> bool:
    """Check if a flag takes its argument as a separate token."""
    return flag in (separated_arg_flags or DEFAULT_SEPARATED_ARG_FLAGS)


def deduplicate_flags(
    flags: Sequence[str | FlagPair],
    separated_arg_flags: frozenset[str] | None = None,
    passthrough_flags: frozenset[str] | None = None,
) -> list[str]:
    """De-duplicate a list of flags, first occurrence wins, order preserved.

    Flags in *separated_arg_flags* and FlagPair objects de-duplicate as
    flag+argument pairs; other flags de-duplicate as single tokens.
    Pass-through flags (e.g. -Xlinker) are kept verbatim and never
    de-duplicated: consecutive ones form one directive (``-Xlinker -rpath
    -Xlinker /p`` is ``-rpath /p`` to the linker), so de-duping a repeated
    ``-Xlinker -rpath`` would orphan its path.

    Returns:
        De-duplicated flags with FlagPairs expanded to strings.

    Examples:
        >>> gcc_flags = frozenset(["-F", "-framework", "-I"])
        >>> deduplicate_flags(["-O2", "-Wall", "-O2"], gcc_flags)
        ['-O2', '-Wall']

        >>> deduplicate_flags(["-F", "path1", "-F", "path2"], gcc_flags)
        ['-F', 'path1', '-F', 'path2']
    """
    if not flags:
        return []

    if separated_arg_flags is None:
        separated_arg_flags = DEFAULT_SEPARATED_ARG_FLAGS

    result: list[str] = []
    seen: set[str | tuple[str, str]] = set()
    i = 0

    while i < len(flags):
        flag = flags[i]

        # Handle FlagPair marker objects
        if isinstance(flag, FlagPair):
            pair = (flag.flag, flag.argument)
            if pair not in seen:
                seen.add(pair)
                result.append(flag.flag)
                result.append(flag.argument)
            i += 1
            continue

        # Pass-through driver flags (e.g. -Xlinker): keep flag+arg verbatim.
        if passthrough_flags and flag in passthrough_flags and i + 1 < len(flags):
            next_item = flags[i + 1]
            result.append(flag)
            if isinstance(next_item, FlagPair):
                result.append(next_item.flag)
                result.append(next_item.argument)
            else:
                result.append(next_item)
            i += 2
            continue

        # Check if this is a flag that takes a separate argument
        if is_separated_arg_flag(flag, separated_arg_flags) and i + 1 < len(flags):
            # Get the argument (must be a string, not a FlagPair)
            next_item = flags[i + 1]
            if isinstance(next_item, FlagPair):
                # The separated arg flag is followed by a FlagPair, treat flag as simple
                if flag not in seen:
                    seen.add(flag)
                    result.append(flag)
                i += 1
            else:
                arg = next_item
                # Create a pair for de-duplication
                pair = (flag, arg)
                if pair not in seen:
                    seen.add(pair)
                    result.append(flag)
                    result.append(arg)
                i += 2
        else:
            # Simple flag or flag with attached argument
            if flag not in seen:
                seen.add(flag)
                result.append(flag)
            i += 1

    return result


def merge_flags(
    existing: MutableSequence[str | PathToken],
    new: Sequence[str | FlagPair | PathToken],
    separated_arg_flags: frozenset[str] | None = None,
) -> None:
    """Merge new flags into *existing* in place, skipping duplicates.

    Separated-argument flags and FlagPairs compare as flag+argument pairs;
    FlagPairs are expanded to strings when appended.

    Examples:
        >>> gcc_flags = frozenset(["-F"])
        >>> existing = ["-O2", "-F", "path1"]
        >>> merge_flags(existing, ["-Wall", "-F", "path1", "-F", "path2"], gcc_flags)
        >>> existing
        ['-O2', '-F', 'path1', '-Wall', '-F', 'path2']
    """
    if not new:
        return

    if separated_arg_flags is None:
        separated_arg_flags = DEFAULT_SEPARATED_ARG_FLAGS

    # Build a set of what's already in existing. Entries are normally strings
    # (or flag/arg tuples), but a PathToken may appear as a complete token.
    existing_items: set[Any] = set()
    i = 0
    while i < len(existing):
        flag = existing[i]
        if is_separated_arg_flag(flag, separated_arg_flags) and i + 1 < len(existing):
            existing_items.add((flag, existing[i + 1]))
            i += 2
        else:
            existing_items.add(flag)
            i += 1

    # Add new items that aren't already present
    # FlagPair objects are expanded to strings when appending
    i = 0
    while i < len(new):
        flag = new[i]
        # Handle FlagPair in new - expand to strings
        if isinstance(flag, FlagPair):
            pair = (flag.flag, flag.argument)
            if pair not in existing_items:
                existing_items.add(pair)
                existing.append(flag.flag)
                existing.append(flag.argument)
            i += 1
        elif is_separated_arg_flag(flag, separated_arg_flags) and i + 1 < len(new):
            next_item = new[i + 1]
            if isinstance(next_item, FlagPair):
                # Separated arg flag followed by FlagPair - treat flag as simple
                if flag not in existing_items:
                    existing_items.add(flag)
                    existing.append(flag)
                i += 1
            else:
                arg = next_item
                pair = (flag, arg)
                if pair not in existing_items:
                    existing_items.add(pair)
                    existing.append(flag)
                    existing.append(arg)
                i += 2
        else:
            if flag not in existing_items:
                existing_items.add(flag)
                existing.append(flag)
            i += 1


def get_separated_arg_flags_from_toolchains(
    toolchains: Iterable[Any],
) -> frozenset[str]:
    """Return the union of all toolchains' separated-argument flags."""
    all_flags: set[str] = set()
    for toolchain in toolchains:
        if hasattr(toolchain, "get_separated_arg_flags"):
            flags = toolchain.get_separated_arg_flags()
            if flags:
                all_flags.update(flags)
    return frozenset(all_flags)
