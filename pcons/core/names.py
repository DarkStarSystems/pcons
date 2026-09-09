# SPDX-License-Identifier: MIT
"""The character rule shared by every name that reaches a build file.

Target names end up in ninja paths and rule names, environment names end up
in ``name@env`` specs, so both must avoid the characters that would make
either unparseable.
"""

from __future__ import annotations

import re

_INVALID_NAME_RE = re.compile(r"[^\w./+-]")


def validate_name(kind: str, name: str) -> None:
    """Raise ValueError unless *name* is well-formed for *kind*.

    Args:
        kind: What is being named, capitalised, e.g. ``"Target"``.
        name: The candidate name.
    """
    if not name:
        raise ValueError(f"{kind} name must not be empty.")
    bad = _INVALID_NAME_RE.findall(name)
    if bad:
        chars = "".join(sorted(set(bad)))
        raise ValueError(
            f"{kind} name {name!r} contains invalid characters: {chars!r}. "
            f"{kind} names may contain letters, digits, underscores, dots, "
            f"plus signs, hyphens, and forward slashes."
        )
