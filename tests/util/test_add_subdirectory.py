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


class TestSubprojectNodePaths:
    """Node paths in a subproject are anchored at the top-level root.

    A subproject's ``root_dir`` is the top-level root, with its own offset
    held in ``_subdir``. Canonicalizing an absolute path against the
    subproject's *current* directory instead drops that offset, so the node
    records a path missing the subproject directory and the build fails with
    a missing-source error. Relative sources never hit it because they are
    explicitly prefixed with ``_subdir``, which is why this stayed latent.
    """

    CHILD = (
        "from pathlib import Path\n"
        "from pcons.core.project import Project\n"
        "project = Project('child')\n"
        "abs_node = project.node(Path(__file__).parent / 'src/foo.c')\n"
        "dir_abs = project.dir_node(Path(__file__).parent / 'include')\n"
        "external = project.node(Path(__file__).parent.parent.parent / 'outside/x.c')\n"
    )

    def test_absolute_source_keeps_subproject_dir(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", self.CHILD)

        ns = add_subdirectory("child")

        assert ns.abs_node.path == Path("child/src/foo.c")

    def test_absolute_and_relative_sources_agree(self, test_project: Project) -> None:
        """Naming one source either way must yield one node, not two.

        ``Target.add_sources`` prefixes ``_subdir`` onto relative specs
        before creating the node, so the two spellings only meet if
        absolute paths anchor at the same place. When they don't, a file
        used by two targets compiles twice under different paths.
        """
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pathlib import Path\n"
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "env = project.parent.default_environment\n"
            "rel = project.StaticLibrary('rel', env, sources=['src/foo.c'])\n"
            "abs_ = project.StaticLibrary('abs', env,\n"
            "    sources=[Path(__file__).parent / 'src/foo.c'])\n",
        )

        ns = add_subdirectory("child")

        assert ns.rel.sources[0] is ns.abs_.sources[0]
        assert ns.rel.sources[0].path == Path("child/src/foo.c")

    def test_absolute_dir_node_keeps_subproject_dir(
        self, test_project: Project
    ) -> None:
        """dir_node() shares the canonicalization path, so it must agree."""
        _make_subdir(test_project, "child", self.CHILD)

        ns = add_subdirectory("child")

        assert ns.dir_abs.path == Path("child/include")

    def test_path_outside_top_root_stays_absolute(self, test_project: Project) -> None:
        """Anchoring only applies under the root; external paths pass through."""
        _make_subdir(test_project, "child", self.CHILD)

        ns = add_subdirectory("child")

        assert ns.external.path.is_absolute()

    def test_two_levels_deep(self, test_project: Project) -> None:
        """The offset is the full path from the top root, not just one level."""
        inner = (
            "from pathlib import Path\n"
            "from pcons.core.project import Project\n"
            "project = Project('inner')\n"
            "abs_node = project.node(Path(__file__).parent / 'src/deep.c')\n"
        )
        _make_subdir(test_project, "a/aa", inner)
        _make_subdir(
            test_project,
            "a",
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('aa')\n",
        )

        ns = add_subdirectory("a")

        assert ns.inner.abs_node.path == Path("a/aa/src/deep.c")

    def test_top_level_absolute_path_unchanged(self, test_project: Project) -> None:
        """Top-level projects have no offset, so behaviour is unaffected."""
        node = test_project.node(test_project.root_dir / "src/main.c")

        assert node.path == Path("src/main.c")


class TestSubprojectDirectories:
    """root_dir and build_dir mean "this project's own" in a subproject.

    A library script reads project.root_dir / project.build_dir to find its
    own sources and to place generated files. If those pointed at the
    top-level project instead, a script that works standalone would silently
    read and write the wrong directories once embedded.
    """

    REPORTER = (
        "from pcons.core.project import Project\n"
        "project = Project('{name}')\n"
        "root = project.root_dir\n"
        "build = project.build_dir\n"
    )

    def test_subproject_dirs_are_its_own(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", self.REPORTER.format(name="child"))

        ns = add_subdirectory("child")

        assert ns.root == test_project.root_dir / "child"
        assert ns.build == test_project.build_dir / "child"

    def test_parallel_subdirs_stay_separate(self, test_project: Project) -> None:
        """Sibling subprojects must not bleed into each other's directories."""
        _make_subdir(test_project, "alpha", self.REPORTER.format(name="alpha"))
        _make_subdir(test_project, "beta", self.REPORTER.format(name="beta"))

        alpha = add_subdirectory("alpha")
        beta = add_subdirectory("beta")

        assert alpha.root == test_project.root_dir / "alpha"
        assert beta.root == test_project.root_dir / "beta"
        assert alpha.build == test_project.build_dir / "alpha"
        assert beta.build == test_project.build_dir / "beta"
        assert alpha.build != beta.build

    def test_two_levels_compose(self, test_project: Project) -> None:
        """A subproject of a subproject accumulates both offsets."""
        _make_subdir(test_project, "a/aa", self.REPORTER.format(name="aa"))
        _make_subdir(
            test_project,
            "a",
            "from pcons.core.project import Project\n"
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "project = Project('a')\n"
            "root = project.root_dir\n"
            "build = project.build_dir\n"
            "inner = add_subdirectory('aa')\n",
        )

        ns = add_subdirectory("a")

        assert ns.root == test_project.root_dir / "a"
        assert ns.build == test_project.build_dir / "a"
        assert ns.inner.root == test_project.root_dir / "a" / "aa"
        assert ns.inner.build == test_project.build_dir / "a" / "aa"

    def test_top_level_dirs_unchanged(self, test_project: Project) -> None:
        """The top-level project keeps plain root/build directories."""
        assert test_project.build_dir == Path("build")
        assert test_project._node_offset.parts == ()

    def test_parallel_subdir_targets_get_distinct_paths(
        self, test_project: Project
    ) -> None:
        """Same-named sources in sibling subprojects stay distinct nodes."""
        test_project.Environment(toolchain="c")
        script = (
            "from pcons.core.project import Project\n"
            "project = Project('{name}')\n"
            "env = project.parent.default_environment\n"
            "lib = project.StaticLibrary('{name}', env, sources=['src/x.c'])\n"
        )
        _make_subdir(test_project, "alpha", script.format(name="alpha"))
        _make_subdir(test_project, "beta", script.format(name="beta"))

        alpha = add_subdirectory("alpha")
        beta = add_subdirectory("beta")

        assert alpha.lib.sources[0].path == Path("alpha/src/x.c")
        assert beta.lib.sources[0].path == Path("beta/src/x.c")
