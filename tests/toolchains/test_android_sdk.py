# SPDX-License-Identifier: MIT
"""Reading an Android SDK: which build-tools revision, and which program.

Nothing here needs an SDK installed. A revision is a directory named after
a version, so a tree of empty directories is the whole of what is read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.configure.platform import get_platform
from pcons.toolchains.android import build_tools_program, newest_build_tools


def _program_suffix() -> str:
    """How the SDK spells an executable on this host."""
    return ".bat" if get_platform().is_windows else ""


def _sdk(root: Path, *revisions: str, program: str | None = None) -> Path:
    """An SDK holding *revisions*, each with *program* installed."""
    for revision in revisions:
        directory = root / "build-tools" / revision
        directory.mkdir(parents=True)
        if program is not None:
            (directory / f"{program}{_program_suffix()}").write_text("")
    return root


class TestTheNewestRevision:
    def test_it_is_ordered_as_a_version_and_not_as_a_string(self, tmp_path) -> None:
        """Sorted as text, "9.0.0" sits above "37.0.0" and is four years
        older, so a build reading it by name signs with the wrong tools."""
        sdk = _sdk(tmp_path, "9.0.0", "37.0.0")

        assert newest_build_tools(sdk).name == "37.0.0"

    def test_a_revision_that_is_not_a_number_does_not_break_the_ordering(
        self, tmp_path
    ) -> None:
        """The SDK manager writes previews as "34.0.0-rc3"."""
        sdk = _sdk(tmp_path, "34.0.0-rc3", "35.0.1")

        assert newest_build_tools(sdk).name == "35.0.1"

    def test_a_stray_file_is_not_a_revision(self, tmp_path) -> None:
        sdk = _sdk(tmp_path, "35.0.1")
        (sdk / "build-tools" / "99.0.0").write_text("")

        assert newest_build_tools(sdk).name == "35.0.1"

    def test_an_sdk_with_none_installed_says_so(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="No build-tools revision"):
            newest_build_tools(tmp_path / "empty")


class TestOneProgram:
    def test_it_comes_from_the_newest_revision(self, tmp_path) -> None:
        sdk = _sdk(tmp_path, "9.0.0", "37.0.0", program="apksigner")

        found = build_tools_program(sdk, "apksigner")

        assert found.parent.name == "37.0.0"
        assert found.is_file()

    def test_a_newer_revision_without_it_is_passed_over(self, tmp_path) -> None:
        """A revision predating the program, or a half-finished install, has
        every other tool and not this one."""
        sdk = _sdk(tmp_path, "30.0.3", program="apksigner")
        (sdk / "build-tools" / "37.0.0").mkdir()

        assert build_tools_program(sdk, "apksigner").parent.name == "30.0.3"

    def test_no_revision_holds_it(self, tmp_path) -> None:
        sdk = _sdk(tmp_path, "37.0.0")

        with pytest.raises(ValueError, match="No zipalign"):
            build_tools_program(sdk, "zipalign")

    def test_it_is_named_the_way_this_host_spells_it(self, tmp_path) -> None:
        """apksigner is a .bat wrapper on Windows and has no extension
        anywhere else, and the path has to be the one that runs."""
        sdk = _sdk(tmp_path, "37.0.0", program="apksigner")

        found = build_tools_program(sdk, "apksigner")

        assert found.name == f"apksigner{_program_suffix()}"
