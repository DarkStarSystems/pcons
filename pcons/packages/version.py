# SPDX-License-Identifier: MIT
"""Version requirements as find_package() takes them: ">=1.2", "<2", "1.2.3"."""

from __future__ import annotations

import re
from itertools import zip_longest

# Longest first, so ">=1.2" is not read as ">" and "=1.2".
_OPERATORS = (">=", "<=", ">", "<", "=")


def _version_key(version: str) -> list[int | str]:
    """A version's numeric and alphabetic parts, in order, for comparing."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.findall(r"\d+|[A-Za-z]+", version)
    ]


def compare_versions(left: str, right: str) -> int:
    """-1, 0 or 1 as *left* is older than, the same as, or newer than *right*.

    Missing parts count as 0, so "1.2" and "1.2.0" are the same version.
    """
    pairs = zip_longest(_version_key(left), _version_key(right), fillvalue=0)
    for left_part, right_part in pairs:
        if left_part == right_part:
            continue
        if isinstance(left_part, int) and isinstance(right_part, int):
            return -1 if left_part < right_part else 1
        return -1 if str(left_part) < str(right_part) else 1
    return 0


def satisfies(version: str, requirement: str) -> bool:
    """Whether *version* meets *requirement*; a bare version means "=" it."""
    requirement = requirement.strip()
    op = next((op for op in _OPERATORS if requirement.startswith(op)), "=")
    wanted = requirement.removeprefix(op).strip()
    order = compare_versions(version, wanted)
    return {
        ">=": order >= 0,
        ">": order > 0,
        "<=": order <= 0,
        "<": order < 0,
        "=": order == 0,
    }[op]
