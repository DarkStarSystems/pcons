# SPDX-License-Identifier: MIT
"""Flag handling utilities for pcons.

This module provides utilities for working with compiler and linker flags,
particularly for de-duplicating flags that take arguments.

The key insight is that flags like -I, -D, -F, -L, -framework, etc. take
an argument that may be either attached (e.g., -Ipath) or separate (e.g., -I path).
When de-duplicating flags, we need to treat the flag+argument pair as a unit.

Note: The actual flag definitions are now maintained in the toolchain classes
(see pcons/toolchains/*.py). The functions in this module accept the flag set
as a parameter, allowing toolchain-specific behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from typing import Any


@dataclass(frozen=True)
class FlagPair:
    """A flag and its argument, kept together during deduplication.

    Use this to explicitly mark flag+argument pairs that should be kept
    together during deduplication, even if the flag isn't in the toolchain's
    SEPARATED_ARG_FLAGS list.

    Example:
        from pcons import FlagPair

        env.cxx.flags.append(FlagPair("-custom-flag", "value"))

        # Equivalent to adding "-custom-flag" to separated_arg_flags
        # but only for this specific pair
    """

    flag: str
    argument: str

    def __iter__(self) -> Iterator[str]:
        """Allow unpacking and iteration: flag, arg = FlagPair(...)"""
        return iter([self.flag, self.argument])


# Default separated arg flags (empty set).
# The actual flags are defined in toolchain classes.
# This is provided for backwards compatibility and for cases
# where no toolchain is available.
DEFAULT_SEPARATED_ARG_FLAGS: frozenset[str] = frozenset()


def is_separated_arg_flag(
    flag: str, separated_arg_flags: frozenset[str] | None = None
) -> bool:
    """Check if a flag takes its argument as a separate token.

    Args:
        flag: The flag to check.
        separated_arg_flags: Set of flags that take separate arguments.
                           If None, uses DEFAULT_SEPARATED_ARG_FLAGS (empty).

    Returns:
        True if this flag expects its argument in the next token.

    Examples:
        >>> gcc_flags = frozenset(["-F", "-framework", "-arch"])
        >>> is_separated_arg_flag("-F", gcc_flags)
        True
        >>> is_separated_arg_flag("-framework", gcc_flags)
        True
        >>> is_separated_arg_flag("-O2", gcc_flags)
        False
    """
    if separated_arg_flags is None:
        separated_arg_flags = DEFAULT_SEPARATED_ARG_FLAGS
    return flag in separated_arg_flags


def deduplicate_flags(
    flags: Sequence[str | FlagPair], separated_arg_flags: frozenset[str] | None = None
) -> list[str]:
    """De-duplicate a list of flags, preserving flag+argument pairs.

    This function handles:
    1. Simple flags like -O2, -Wall: de-duplicated individually
    2. Flags with attached arguments like -DFOO, -Ipath: de-duplicated as complete tokens
    3. Flags with separate arguments like -F path, -framework Foo: de-duplicated as pairs
    4. FlagPair objects: treated as atomic flag+argument pairs

    The function preserves order (first occurrence wins) and handles the case where
    a flag might appear both with and without an argument (unusual but possible).

    Args:
        flags: List of flag strings or FlagPair objects.
        separated_arg_flags: Set of flags that take separate arguments.
                           If None, uses DEFAULT_SEPARATED_ARG_FLAGS (empty).

    Returns:
        De-duplicated list of flags with order preserved (FlagPairs expanded to strings).

    Examples:
        >>> gcc_flags = frozenset(["-F", "-framework", "-I"])
        >>> deduplicate_flags(["-O2", "-Wall", "-O2"], gcc_flags)
        ['-O2', '-Wall']

        >>> deduplicate_flags(["-I", "path1", "-I", "path1"], gcc_flags)
        ['-I', 'path1']

        >>> deduplicate_flags(["-F", "path1", "-F", "path2"], gcc_flags)
        ['-F', 'path1', '-F', 'path2']

        >>> deduplicate_flags(["-framework", "Cocoa", "-framework", "CoreFoundation"], gcc_flags)
        ['-framework', 'Cocoa', '-framework', 'CoreFoundation']

        >>> deduplicate_flags([FlagPair("-custom", "val1"), FlagPair("-custom", "val1")])
        ['-custom', 'val1']
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
    existing: list[str],
    new: Sequence[str | FlagPair],
    separated_arg_flags: frozenset[str] | None = None,
) -> None:
    """Merge new flags into existing list, avoiding duplicates.

    This modifies `existing` in place, adding flags from `new` that
    aren't already present. It properly handles flags with separate arguments
    and FlagPair objects. FlagPair objects are expanded to their component
    strings when appended.

    Args:
        existing: List of existing flags (modified in place). Should contain strings.
        new: List of new flags to merge in. May contain strings or FlagPair objects.
        separated_arg_flags: Set of flags that take separate arguments.
                           If None, uses DEFAULT_SEPARATED_ARG_FLAGS (empty).

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

    # Build a set of what's already in existing
    # existing should only contain strings at this point
    existing_items: set[str | tuple[str, str]] = set()
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
    """Collect separated arg flags from all toolchains.

    This function queries each toolchain for its separated arg flags
    and returns the union of all flags.

    Args:
        toolchains: Iterable of toolchain objects that may have
                   get_separated_arg_flags() method.

    Returns:
        Union of all separated arg flags from all toolchains.
    """
    all_flags: set[str] = set()
    for toolchain in toolchains:
        if hasattr(toolchain, "get_separated_arg_flags"):
            flags = toolchain.get_separated_arg_flags()
            if flags:
                all_flags.update(flags)
    return frozenset(all_flags)
