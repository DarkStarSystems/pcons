# SPDX-License-Identifier: MIT
"""What every part of PyBuilder raises, and the helpers its messages share."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.core.errors import PconsError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class PyBuilderError(PconsError):
    """A function cannot be turned into a build edge."""


def _and_list(names: Sequence[str]) -> str:
    """Names as a reader would say them, with the last joined by "and"."""
    if len(names) < 2:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _describe(fn: Callable[..., object]) -> str:
    """Name a callable the way an error message should."""
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    return f"{name}()" if name else repr(fn)
