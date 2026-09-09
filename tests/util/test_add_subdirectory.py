# SPDX-License-Identifier: MIT
"""Tests for pcons.util.add_subdirectory."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcons import get_var
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.util.add_subdirectory import add_subdirectory


def _make_subdir(parent: Path | Project, name: str, content: str) -> Path:
    """Create a subdirectory with a pcons-build.py script."""
    parent = parent.root_dir if isinstance(parent, Project) else parent
    subdir = parent / name
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "pcons-build.py").write_text(content)
    return subdir


class TestSubdirectoryVars:
    """`vars=` configures what an inclusion reads with `get_var`."""

    def test_the_script_reads_what_the_caller_passed(
        self, test_project: Project
    ) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons import get_var\nvalue = get_var('FEATURE', True)\n",
        )

        ns = add_subdirectory("child", vars={"FEATURE": False})

        assert ns.value is False

    def test_two_inclusions_read_their_own(self, test_project: Project) -> None:
        """The value is read by an imported module, at import time."""
        subdir = _make_subdir(
            test_project, "child", "import settings\nvalue = settings.FEATURE\n"
        )
        (subdir / "settings.py").write_text(
            "from pcons import get_var\nFEATURE = get_var('FEATURE', True)\n"
        )

        off = add_subdirectory("child", vars={"FEATURE": False})
        on = add_subdirectory("child", vars={"FEATURE": True})

        assert (off.value, on.value) == (False, True)

    def test_it_is_gone_afterwards(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", "x = 1\n")

        add_subdirectory("child", vars={"FEATURE": False})

        assert get_var("FEATURE", True) is True

    def test_the_project_method_forwards_them(self, test_project: Project) -> None:
        _make_subdir(
            test_project,
            "child",
            "from pcons import get_var\nvalue = get_var('FEATURE', True)\n",
        )

        ns = test_project.add_subdirectory("child", vars={"FEATURE": False})

        assert ns.value is False


class TestSubdirectoryScriptImports:
    """A subdirectory script reaches its own neighbours, like a root script does."""

    def test_it_imports_a_module_beside_it(self, test_project: Project) -> None:
        subdir = _make_subdir(
            test_project, "child", "import helper\nvalue = helper.V\n"
        )
        (subdir / "helper.py").write_text("V = 'from the child'\n")

        ns = add_subdirectory("child")

        assert ns.value == "from the child"

    def test_a_nested_script_imports_its_own(self, test_project: Project) -> None:
        outer = _make_subdir(
            test_project,
            "outer",
            "import outer_helper\n"
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('inner')\n"
            "value = (outer_helper.V, inner.value)\n",
        )
        (outer / "outer_helper.py").write_text("V = 'outer'\n")
        inner = _make_subdir(
            outer, "inner", "import inner_helper\nvalue = inner_helper.V\n"
        )
        (inner / "inner_helper.py").write_text("V = 'inner'\n")

        ns = add_subdirectory("outer")

        assert ns.value == ("outer", "inner")

    def test_two_subdirectories_keep_their_own(self, test_project: Project) -> None:
        for name in ("a", "b"):
            subdir = _make_subdir(
                test_project, name, "import sources\nvalue = sources.NAME\n"
            )
            (subdir / "sources.py").write_text(f"NAME = 'from-{name}'\n")

        first = add_subdirectory("a")
        second = add_subdirectory("b")

        assert (first.value, second.value) == ("from-a", "from-b")
        assert "sources" not in sys.modules

    def test_a_second_inclusion_imports_again(self, test_project: Project) -> None:
        subdir = _make_subdir(
            test_project, "child", "import counter\nvalue = counter.PASS\n"
        )
        (subdir / "counter.py").write_text(
            "import itertools\n_seq = itertools.count(1)\nPASS = next(_seq)\n"
        )

        first = add_subdirectory("child")
        second = add_subdirectory("child")

        assert (first.value, second.value) == (1, 1)

    def test_a_regular_package_is_released_with_its_submodules(
        self, test_project: Project
    ) -> None:
        for name in ("a", "b"):
            subdir = _make_subdir(
                test_project,
                name,
                "import shapes.box\nvalue = (shapes.NAME, shapes.box.NAME)\n",
            )
            package = subdir / "shapes"
            package.mkdir()
            (package / "__init__.py").write_text(f"NAME = 'pkg-{name}'\n")
            (package / "box.py").write_text(f"NAME = 'box-{name}'\n")

        first = add_subdirectory("a")
        second = add_subdirectory("b")

        assert first.value == ("pkg-a", "box-a")
        assert second.value == ("pkg-b", "box-b")
        assert not [n for n in sys.modules if n.split(".")[0] == "shapes"]

    def test_a_namespace_package_is_released_too(self, test_project: Project) -> None:
        """No `__init__.py`, so no `__file__`: its search locations say where."""
        for name in ("a", "b"):
            subdir = _make_subdir(
                test_project, name, "import parts.leaf\nvalue = parts.leaf.NAME\n"
            )
            package = subdir / "parts"
            package.mkdir()
            (package / "leaf.py").write_text(f"NAME = 'leaf-{name}'\n")

        first = add_subdirectory("a")
        second = add_subdirectory("b")

        assert (first.value, second.value) == ("leaf-a", "leaf-b")
        assert not [n for n in sys.modules if n.split(".")[0] == "parts"]

    def test_package_sources_are_configure_dependencies(
        self, test_project: Project
    ) -> None:
        subdir = _make_subdir(test_project, "child", "import shapes.box\n")
        package = subdir / "shapes"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "box.py").write_text("NAME = 'box'\n")

        add_subdirectory("child")

        deps = {
            Path(p).parent.name + "/" + Path(p).name
            for p in test_project.configure_dependencies
        }
        assert {"shapes/__init__.py", "shapes/box.py"} <= deps

    def test_a_module_outside_the_subdirectory_stays_cached(
        self, test_project: Project
    ) -> None:
        (test_project.root_dir / "shared.py").write_text("V = 'root'\n")
        _make_subdir(test_project, "child", "import shared\nvalue = shared.V\n")
        sys.path.insert(0, str(test_project.root_dir))
        try:
            ns = add_subdirectory("child")
        finally:
            sys.path.pop(0)
            sys.modules.pop("shared", None)

        assert ns.value == "root"

    def test_a_released_module_is_a_configure_dependency(
        self, test_project: Project
    ) -> None:
        subdir = _make_subdir(test_project, "child", "import helper\nx = helper.V\n")
        (subdir / "helper.py").write_text("V = 1\n")

        add_subdirectory("child")

        deps = {Path(p).name for p in test_project.configure_dependencies}
        assert "helper.py" in deps

    def test_an_unresolvable_origin_is_left_alone(
        self, test_project: Project, monkeypatch
    ) -> None:
        """A path that cannot be resolved is skipped, not fatal."""
        from pcons.util import add_subdirectory as module

        subdir = _make_subdir(test_project, "child", "import helper\nx = helper.V\n")
        (subdir / "helper.py").write_text("V = 1\n")

        def raising(_module: object) -> list[Path]:
            raise OSError("no")

        monkeypatch.setattr(module, "_module_origins", raising)

        ns = add_subdirectory("child")

        assert ns.x == 1
        sys.modules.pop("helper", None)

    def test_the_path_is_restored(self, test_project: Project) -> None:
        _make_subdir(test_project, "child", "x = 1\n")
        before = list(sys.path)

        add_subdirectory("child")

        assert sys.path == before

    def test_the_path_is_restored_when_the_script_raises(
        self, test_project: Project
    ) -> None:
        _make_subdir(test_project, "child", "raise RuntimeError('boom')\n")
        before = list(sys.path)

        with pytest.raises(RuntimeError, match="boom"):
            add_subdirectory("child")

        assert sys.path == before


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


class TestSubdirectoryEnvironment:
    """``add_subdirectory(..., env=...)`` builds one tree per environment."""

    SUB = (
        "from pcons.core.project import Project\n"
        "project = Project('sub')\n"
        "env = project.parent.default_environment\n"
        "lib = project.StaticLibrary('thing', env, sources=['src/thing.c'])\n"
    )

    @pytest.fixture
    def two_envs(self, test_project: Project, gcc_toolchain):
        host = test_project.Environment(toolchain=gcc_toolchain, name="host")
        host.build_prefix = "host"
        mcu = test_project.Environment(toolchain=gcc_toolchain, name="mcu")
        mcu.build_prefix = "mcu"
        return host, mcu

    def _source(self, subdir: Path) -> None:
        (subdir / "src").mkdir(parents=True, exist_ok=True)
        (subdir / "src" / "thing.c").write_text("int g(void) { return 2; }\n")

    def test_one_subdirectory_builds_once_per_environment(
        self, test_project: Project, two_envs
    ) -> None:
        host, mcu = two_envs
        self._source(_make_subdir(test_project, "sub", self.SUB))

        first = add_subdirectory("sub", env=host)
        second = add_subdirectory("sub", env=mcu)
        test_project.resolve()

        assert first.lib.env is host
        assert second.lib.env is mcu
        prefix = host._toolchain.get_output_prefix("static_library")
        suffix = host._toolchain.get_output_suffix("static_library")
        name = f"{prefix}thing{suffix}"
        assert [n.path.as_posix() for n in first.lib.output_nodes] == [
            f"build/host/sub/{name}"
        ]
        assert [n.path.as_posix() for n in second.lib.output_nodes] == [
            f"build/mcu/sub/{name}"
        ]
        assert test_project.get_target("thing@host") is first.lib
        assert test_project.get_target("thing@mcu") is second.lib

    def test_the_project_method_forwards_the_environment(
        self, test_project: Project, two_envs
    ) -> None:
        host, _mcu = two_envs
        self._source(_make_subdir(test_project, "sub", self.SUB))

        ns = test_project.add_subdirectory("sub", env=host)

        assert ns.lib.env is host

    def test_a_nested_inclusion_inherits_the_environment(
        self, test_project: Project, two_envs
    ) -> None:
        host, _mcu = two_envs
        self._source(_make_subdir(test_project, "sub/inner", self.SUB))
        _make_subdir(
            test_project,
            "sub",
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('inner')\n",
        )

        ns = add_subdirectory("sub", env=host)

        assert ns.inner.lib.env is host

    def test_an_inner_environment_wins_for_its_own_subtree(
        self, test_project: Project, two_envs
    ) -> None:
        host, mcu = two_envs
        self._source(_make_subdir(test_project, "sub/inner", self.SUB))
        sub = _make_subdir(
            test_project,
            "sub",
            "from pcons.core.project import Project\n"
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "project = Project('sub')\n"
            "mcu = [e for e in project.top.environments if e.name == 'mcu'][0]\n"
            "inner = add_subdirectory('inner', env=mcu)\n"
            "after = project.parent.default_environment\n"
            "lib = project.StaticLibrary('thing', after, sources=['src/thing.c'])\n",
        )
        self._source(sub)

        ns = add_subdirectory("sub", env=host)

        assert ns.inner.lib.env is mcu
        assert ns.after is host
        assert ns.lib.env is host

    def test_the_environment_is_restored_after_the_inclusion(
        self, test_project: Project, two_envs
    ) -> None:
        _host, mcu = two_envs
        self._source(_make_subdir(test_project, "sub", self.SUB))

        add_subdirectory("sub", env=mcu)

        assert test_project.default_environment is test_project.environments[0]

    def test_an_exception_in_the_sub_script_restores_the_environment(
        self, test_project: Project, two_envs
    ) -> None:
        host, _mcu = two_envs
        _make_subdir(test_project, "sub", "raise RuntimeError('boom')\n")

        with pytest.raises(RuntimeError, match="boom"):
            add_subdirectory("sub", env=host)

        assert test_project.default_environment is test_project.environments[0]

    def test_without_an_environment_nothing_is_overridden(
        self, test_project: Project, two_envs
    ) -> None:
        host, _mcu = two_envs
        self._source(_make_subdir(test_project, "sub", self.SUB))

        ns = add_subdirectory("sub")

        assert ns.lib.env is host
        assert test_project.default_environment is host

    def test_enter_subdir_without_an_environment_changes_nothing(
        self, test_project: Project, two_envs
    ) -> None:
        host, _mcu = two_envs

        with test_project._enter_subdir("sub"):
            assert test_project.default_environment is host
        assert test_project.default_environment is host

    def test_a_nested_enter_subdir_keeps_the_outer_environment(
        self, test_project: Project, two_envs
    ) -> None:
        _host, mcu = two_envs

        with test_project._enter_subdir("sub", env=mcu):
            with test_project._enter_subdir("inner"):
                assert test_project.default_environment is mcu
            assert test_project.default_environment is mcu
        assert test_project.default_environment is test_project.environments[0]

    def test_the_override_survives_a_project_with_its_own_environments(
        self, test_project: Project, two_envs, gcc_toolchain
    ) -> None:
        """A sub-project registering an environment still gets the override."""
        _host, mcu = two_envs

        with test_project._enter_subdir("sub", env=mcu):
            child = Project("child", root_dir=test_project.root_dir)
            child.Environment(toolchain=gcc_toolchain, name="own")
            assert child.default_environment is mcu


class TestSubdirectoryCommandPaths:
    """``env.Command`` anchors both ends at the script that declares it.

    A generator declared in a subdirectory writes into that subdirectory's
    build directory and reads its inputs from that subdirectory, the same
    way ``Program`` and ``StaticLibrary`` place their objects. Anchoring
    either end at the top-level project instead puts the output in the
    wrong place and names a source file that does not exist.
    """

    @staticmethod
    def _ninja(project: Project, tmp_path: Path) -> str:
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    CHILD = (
        "from pcons.core.project import Project\n"
        "project = Project.current()\n"
        "env = project.default_environment\n"
        "gen = env.Command(\n"
        "    target='gen/extra.h',\n"
        "    source='mk.py',\n"
        "    command='python3 $SOURCE $TARGET',\n"
        ")\n"
    )

    def test_command_target_lands_in_subdir_build_dir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(test_project, "child", self.CHILD)

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "build child/gen/extra.h: " in text

    def test_command_source_anchors_at_declaring_subdir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(test_project, "child", self.CHILD)

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "$topdir/child/mk.py" in text
        assert "$topdir/mk.py" not in text

    def test_nested_subdirectories_accumulate(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(test_project, "a/aa", self.CHILD)
        _make_subdir(
            test_project,
            "a",
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "inner = add_subdirectory('aa')\n",
        )

        add_subdirectory("a")
        text = self._ninja(test_project, tmp_path)

        assert "build a/aa/gen/extra.h: " in text
        assert "$topdir/a/aa/mk.py" in text

    def test_subproject_with_own_project_object(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        """A subdir that creates its own Project anchors the same way.

        Its ``build_dir`` already carries the offset, so the anchor must not
        apply it twice.
        """
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project('child')\n"
            "env = project.Environment(toolchain='c')\n"
            "gen = env.Command(\n"
            "    target='gen/extra.h',\n"
            "    source='mk.py',\n"
            "    command='python3 $SOURCE $TARGET',\n"
            ")\n",
        )

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "build child/gen/extra.h: " in text
        assert "$topdir/child/mk.py" in text

    def test_nested_subproject_inheriting_the_parent_environment(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        """Two levels down, on an environment the top-level project owns.

        The environment reached through ``project.parent`` belongs to the
        top-level project, so its own offset is empty. The anchor is the
        declaring script's, not the environment owner's, or the same edge
        contradicts itself: the target and the source land one level down
        while ``depends=`` lands two.
        """
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "a/aa",
            "from pcons.core.project import Project\n"
            "project = Project('aa')\n"
            "env = project.parent.default_environment\n"
            "gen = env.Command(\n"
            "    target='gen/extra.h',\n"
            "    source='mk.py',\n"
            "    command='python3 $SOURCE $TARGET',\n"
            "    depends='schema.json',\n"
            ")\n",
        )
        _make_subdir(
            test_project,
            "a",
            "from pcons.core.project import Project\n"
            "from pcons.util.add_subdirectory import add_subdirectory\n"
            "project = Project('a')\n"
            "inner = add_subdirectory('aa')\n",
        )

        add_subdirectory("a")
        text = self._ninja(test_project, tmp_path)

        assert "build a/aa/gen/extra.h: " in text
        assert "$topdir/a/aa/mk.py" in text
        assert "$topdir/a/aa/schema.json" in text

    def test_top_level_command_unchanged(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        env = test_project.Environment(toolchain="c")
        env.Command(
            target="gen/extra.h",
            source="mk.py",
            command="python3 $SOURCE $TARGET",
        )

        text = self._ninja(test_project, tmp_path)

        assert "build gen/extra.h: " in text
        assert "$topdir/mk.py" in text

    def test_command_depends_anchors_at_declaring_subdir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project.current()\n"
            "env = project.default_environment\n"
            "gen = env.Command(\n"
            "    target='gen/extra.h',\n"
            "    source='mk.py',\n"
            "    command='python3 $SOURCE $TARGET',\n"
            "    depends='schema.json',\n"
            ")\n",
        )

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "$topdir/child/schema.json" in text


class TestSubdirectoryInstallPaths:
    """Install and archive sources anchor at the declaring script too.

    These builders keep their sources pending until resolution, long after
    the subdirectory script finished, so the offset has to come from the
    target rather than from the project's current directory.
    """

    @staticmethod
    def _ninja(project: Project, tmp_path: Path) -> str:
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    def test_install_source_anchors_at_declaring_subdir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project.current()\n"
            "inst = project.Install('share', ['data.txt'])\n",
        )

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "$topdir/child/data.txt" in text

    def test_install_as_source_anchors_at_declaring_subdir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project.current()\n"
            "inst = project.InstallAs('share/renamed.txt', 'data.txt')\n",
        )

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "$topdir/child/data.txt" in text

    def test_install_dir_source_anchors_at_declaring_subdir(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        _make_subdir(
            test_project,
            "child",
            "from pcons.core.project import Project\n"
            "project = Project.current()\n"
            "inst = project.InstallDir('share/tree', 'assets')\n",
        )

        add_subdirectory("child")
        text = self._ninja(test_project, tmp_path)

        assert "$topdir/child/assets" in text

    def test_top_level_install_unchanged(
        self, test_project: Project, tmp_path: Path
    ) -> None:
        test_project.Environment(toolchain="c")
        test_project.Install("share", ["data.txt"])

        text = self._ninja(test_project, tmp_path)

        assert "$topdir/data.txt" in text
