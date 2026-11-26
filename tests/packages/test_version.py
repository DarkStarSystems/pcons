# SPDX-License-Identifier: MIT
"""Version requirements, shared by find_package() and the pkg-config finder."""

from __future__ import annotations

import pytest

from pcons.packages.version import compare_versions, satisfies


@pytest.mark.parametrize(
    ("left", "right", "order"),
    [
        ("1.2.3", "1.2.3", 0),
        ("1.2", "1.2.0", 0),
        ("1.10", "1.9", 1),
        ("1.2.13", "1.3", -1),
        ("2.0rc1", "2.0RC1", 0),
        ("2.0a", "2.0b", -1),
    ],
)
def test_compare_versions(left: str, right: str, order: int) -> None:
    assert compare_versions(left, right) == order


@pytest.mark.parametrize(
    ("requirement", "met"),
    [
        (">=1.2", True),
        (">= 1.3", True),
        (">1.3.1", False),
        ("<2", True),
        ("<=1.3", False),
        ("=1.3.1", True),
        ("1.3.1", True),
        ("1.3", False),
    ],
)
def test_satisfies(requirement: str, met: bool) -> None:
    assert satisfies("1.3.1", requirement) is met
