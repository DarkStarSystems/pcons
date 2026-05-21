# SPDX-License-Identifier: MIT
"""Tests for pcons.util.add_subdirectory."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pcons.core.project import Project
from pcons.util.add_subdirectory import add_subdirectory


def _make_subdir(parent: Path | Project, name: str, content: str) -> Path:
    """Create a subdirectory with a pcons-build.py script."""
    parent = parent.root_dir if isinstance(parent, Project) else parent
    subdir = parent / name
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "pcons-build.py").write_text(content)
    return subdir


class TestAddSubdirectory:
    def test_returns_namespace_with_exported_names(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", "result = 42\n")

        ns = add_subdirectory("child")

        assert isinstance(ns, SimpleNamespace)
        assert ns.result == 42

    def test_pick_returns_tuple(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", "a = 1\nb = 2\nc = 3\n")

        values = add_subdirectory("child", pick=["a", "c"])

        assert values == (1, 3)

    def test_subdirectory_script_runs_in_project_context(
        self, test_project: Project
    ) -> None:
        """Scripts in subdirs see the same Project via Project.current()."""
        Project("root", root_dir=test_project.root_dir)
        script = (
            "from pcons.core.project import Project\n"
            "found = Project.current() is not None\n"
        )
        _make_subdir(test_project, "child", script)

        ns = add_subdirectory("child")

        assert ns.found is True

    def test_current_dir_set_correctly_inside_subdir(self, test_project: Project):
        script = (
            "from pcons.core.project import Project\n"
            "import pathlib\n"
            "cdir = Project.current().current_dir\n"
        )
        _make_subdir(test_project, "child", script)

        ns = add_subdirectory("child")

        assert ns.cdir == test_project.root_dir / "child"

    def test_current_dir_restored_after_subdir(self, test_project: Project):
        _make_subdir(test_project, "child", "x = 1\n")

        add_subdirectory("child")

        assert test_project.current_dir == test_project.root_dir

    def test_missing_pcons_build_raises(self, test_project: Project) -> None:
        (test_project.root_dir / "empty").mkdir()

        with pytest.raises(FileNotFoundError, match="pcons-build.py"):
            add_subdirectory("empty")

    def test_no_active_project_raises(self, tmp_path: Path) -> None:
        _make_subdir(tmp_path, "child", "x = 1\n")
        # No Project created, so Project.current() raises ValueError
        with pytest.raises(ValueError, match="no project is currently active"):
            add_subdirectory(tmp_path / "child")

    def test_nested_subdirectory(self, test_project: Project) -> None:
        """Two levels of nesting: root -> a -> aa."""
        aa_script = "from pcons.core.project import Project\ncdir = Project.current().current_dir\n"
        _make_subdir(test_project, "a/aa", aa_script)
        a_script = (
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('aa')\n"
        )
        _make_subdir(test_project, "a", a_script)

        ns = add_subdirectory("a")

        assert ns.inner.cdir == test_project.root_dir / "a" / "aa"
