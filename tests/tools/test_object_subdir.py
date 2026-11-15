# SPDX-License-Identifier: MIT
"""Where under its target's object directory a source's object goes."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from pcons.tools.compile_link import object_subdir


@pytest.mark.parametrize(
    ("source_dir", "expected"),
    [
        (PurePosixPath("src/core"), ["src", "core"]),
        (PurePosixPath("../vendor/src"), ["vendor", "src"]),
        # Outside the project: the root goes, the directories stay.
        (
            PurePosixPath("/root/.conan2/p/nb/src"),
            ["root", ".conan2", "p", "nb", "src"],
        ),
        # Before, the drive's root stayed, and joining it onto the object
        # directory put the object beside the source, in another package.
        (
            PureWindowsPath("C:/Users/me/.conan2/p/nb/src"),
            ["C", "Users", "me", ".conan2", "p", "nb", "src"],
        ),
        (PureWindowsPath("D:/Users/me/src"), ["D", "Users", "me", "src"]),
        (PureWindowsPath("//server/share/src"), ["server_share", "src"]),
        (PureWindowsPath("src/core"), ["src", "core"]),
    ],
)
def test_object_subdir(source_dir, expected) -> None:
    assert object_subdir(source_dir) == expected


def test_an_outside_windows_source_stays_under_the_object_dir() -> None:
    obj_dir = PureWindowsPath("E:/proj/build/obj.ext.shared")
    source_dir = PureWindowsPath("C:/Users/me/.conan2/p/nb/src")

    assert obj_dir.joinpath(*object_subdir(source_dir)).is_relative_to(obj_dir)
