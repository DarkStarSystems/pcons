# SPDX-License-Identifier: MIT
"""A target is identified by its name and its environment (#96).

Two targets may share a name when both environments are named and the names
differ: Environment.build_prefix then keeps their files apart.
"""

from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.core.errors import PconsError
from pcons.core.project import Project


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "common.c").write_text("int f(void) { return 1; }\n")
    return src


def _two_envs(project, gcc_toolchain):
    envs = []
    for name in ("mcu", "host"):
        env = project.Environment(toolchain=gcc_toolchain, name=name)
        env.build_prefix = name
        envs.append(env)
    return envs


class TestEnvironmentName:
    def test_assignment_changes_the_name(self, test_project):  # noqa: F811
        env = Environment(name="mcu")
        env.name = "host"

        assert env.name == "host"
        assert "name" not in env._vars

    def test_it_still_substitutes(self, test_project):  # noqa: F811
        env = Environment(name="mcu")

        assert env.subst("building $name") == "building mcu"

    @pytest.mark.parametrize("bad", ["a@b", "x::y", "with space"])
    def test_invalid_characters_are_refused(self, test_project, bad):  # noqa: F811
        with pytest.raises(ValueError, match="Environment name"):
            Environment(name=bad)

        env = Environment(name="ok")
        with pytest.raises(ValueError, match="Environment name"):
            env.name = bad


class TestDuplicateNames:
    def test_two_named_environments_may_share_a_name(
        self, tmp_path, source, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        libs = [
            project.StaticLibrary("common", env, sources=["src/common.c"])
            for env in _two_envs(project, gcc_toolchain)
        ]

        project.resolve()

        paths = {node.path.as_posix() for lib in libs for node in lib.output_nodes}
        prefix = gcc_toolchain.get_output_prefix("static_library")
        suffix = gcc_toolchain.get_output_suffix("static_library")
        name = f"{prefix}common{suffix}"
        assert paths == {f"build/mcu/{name}", f"build/host/{name}"}

    def test_the_order_they_are_declared_in_does_not_matter(
        self, tmp_path, gcc_toolchain
    ):
        """The check reads each target's own environment, not the project's last."""
        project = Project("p", root_dir=tmp_path)
        mcu, host = _two_envs(project, gcc_toolchain)

        first = project.StaticLibrary("common", host)
        second = project.StaticLibrary("common", mcu)

        assert first.env is host
        assert second.env is mcu

    def test_the_same_environment_twice_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        project.StaticLibrary("common", env)

        with pytest.raises(ValueError, match="in environment 'mcu'"):
            project.StaticLibrary("common", env)

    def test_an_unnamed_environment_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        named = project.Environment(toolchain=gcc_toolchain, name="mcu")
        unnamed = project.Environment(toolchain=gcc_toolchain)
        project.StaticLibrary("common", named)

        with pytest.raises(ValueError, match="named and different"):
            project.StaticLibrary("common", unnamed)

    def test_colliding_outputs_still_raise(self, tmp_path, source, gcc_toolchain):
        """Duplicate names are legal because the directories differ, not the names."""
        project = Project("p", root_dir=tmp_path)
        for name in ("mcu", "host"):
            env = project.Environment(toolchain=gcc_toolchain, name=name)
            project.StaticLibrary("common", env, sources=["src/common.c"])

        with pytest.raises(PconsError, match="both build"):
            project.resolve()


class TestEnvironmentNamesAreUnique:
    """A name says which environment a target was built in, so it identifies one."""

    def test_a_second_environment_of_that_name_is_refused(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain, name="mcu")

        with pytest.raises(PconsError, match="already has an environment named"):
            project.Environment(toolchain=gcc_toolchain, name="mcu")

    def test_unnamed_environments_are_unconstrained(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain)
        project.Environment(toolchain=gcc_toolchain)

        assert [env.name for env in project.environments] == [None, None]

    def test_a_sub_project_keeps_its_own_names(self, tmp_path, gcc_toolchain):
        """'child::app@host' is not 'p::app@host', so both may have a host."""
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain, name="host")
        (tmp_path / "child").mkdir()
        with project._enter_subdir("child"):
            child = Project("child", root_dir=tmp_path / "child")
            child.Environment(toolchain=gcc_toolchain, name="host")

        assert [env.name for env in child.environments] == ["host"]

    def test_a_rename_onto_a_taken_name_is_refused(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        project.Environment(toolchain=gcc_toolchain, name="host")
        mcu = project.Environment(toolchain=gcc_toolchain, name="mcu")

        with pytest.raises(PconsError, match="already has an environment named"):
            mcu.name = "host"

        assert mcu.name == "mcu"

    def test_a_rename_to_a_free_name_is_allowed(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="host")

        env.name = "target"

        assert env.name == "target"

    def test_renaming_to_its_own_name_is_not_a_collision(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="host")

        env.name = "host"

        assert env.name == "host"


class TestLookup:
    def test_an_ambiguous_name_raises(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env)

        with pytest.raises(KeyError, match="common@mcu"):
            project.get_target("common")

    def test_a_single_match_still_resolves(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        lib = project.StaticLibrary("common", env)

        assert project.get_target("common") is lib
        assert project.get_target("p::common") is lib

    def test_a_free_name_is_not_taken(self, tmp_path):
        project = Project("p", root_dir=tmp_path)

        assert project.has_target("common") is False

    def test_a_single_match_is_taken(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        project.StaticLibrary("common", env)

        assert project.has_target("common") is True

    def test_an_ambiguous_name_is_taken(self, tmp_path, gcc_toolchain):
        """The lookup refuses to answer it, so the boolean answers for it."""
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env)

        assert project.has_target("common") is True
        with pytest.raises(KeyError, match="Multiple targets named"):
            project.get_target("common", raise_if_missing=False)

    def test_a_sub_project_name_is_taken_only_when_searching_the_tree(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        (tmp_path / "child").mkdir()
        with project._enter_subdir("child"):
            child = Project("child", root_dir=tmp_path / "child")
            env = child.Environment(toolchain=gcc_toolchain, name="mcu")
            child.StaticLibrary("common", env)

        assert project.has_target("common") is True
        assert project.has_target("common", recursive=False) is False


class TestQualifiedSpelling:
    def test_it_names_the_environment(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        lib = project.StaticLibrary("common", env)

        assert lib.env is env
        assert lib.qualified_name == "p::common@mcu"

    def test_an_unnamed_environment_leaves_the_plain_name(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        lib = project.StaticLibrary("common", env)

        assert lib.qualified_name == "p::common"


class TestAmbiguityMessage:
    def test_it_offers_a_spelling_that_selects_one(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env)

        with pytest.raises(KeyError) as excinfo:
            project.get_target("common")

        message = str(excinfo.value)
        assert "p::common@mcu, p::common@host" in message
        assert "Name the environment, e.g. 'p::common@mcu'." in message

    def test_it_asks_for_names_when_no_spelling_would_help(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        for name in ("one", "two"):
            (tmp_path / name).mkdir()
            with project._enter_subdir(name):
                child = Project("child", root_dir=tmp_path / name)
                env = child.Environment(toolchain=gcc_toolchain)
                env.build_prefix = name
                child.StaticLibrary("common", env)

        with pytest.raises(KeyError) as excinfo:
            project.get_target("common")

        message = str(excinfo.value)
        assert "child::common, child::common" in message
        assert "no spelling tells them apart" in message


class TestOneRuleForAMissingEnvironment:
    """`default_environment` and a target's fallback answer the same question.

    They once differed twice over: first versus last registered, and whether
    the search walked up to enclosing projects. A single-environment project
    cannot tell them apart, which is how they drifted.
    """

    def test_they_agree_in_a_multi_environment_project(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        _two_envs(project, gcc_toolchain)

        assert project._inherited_environment() is project.default_environment

    def test_they_agree_on_an_enclosing_projects_environment(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        _two_envs(project, gcc_toolchain)
        (tmp_path / "child").mkdir()
        with project._enter_subdir("child"):
            child = Project("child", root_dir=tmp_path / "child")

        assert child._inherited_environment() is child.default_environment
        assert child._inherited_environment() is project.default_environment

    def test_they_agree_on_the_environment_an_inclusion_named(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        _mcu, host = _two_envs(project, gcc_toolchain)
        (tmp_path / "child").mkdir()
        with project._enter_subdir("child", env=host):
            child = Project("child", root_dir=tmp_path / "child")
            assert child._inherited_environment() is host
            assert child.default_environment is host

    def test_only_the_empty_case_differs(self, tmp_path):
        """One raises and one returns None. That is the whole difference."""
        project = Project("p", root_dir=tmp_path)

        assert project._inherited_environment() is None
        with pytest.raises(ValueError, match="No environments"):
            _ = project.default_environment


class TestTargetsWithoutAnEnvironment:
    """A builder taking no ``env`` still lands in the right environment."""

    def _include(self, project, tmp_path, env):
        (tmp_path / "sub").mkdir(exist_ok=True)
        with project._enter_subdir("sub", env=env):
            child = Project("child", root_dir=tmp_path / "sub")
            return child.HeaderOnlyLibrary("common")

    def test_it_takes_the_included_environment(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        mcu, _host = _two_envs(project, gcc_toolchain)

        iface = self._include(project, tmp_path, mcu)

        assert iface.env is mcu
        assert iface.qualified_name == "child::common@mcu"

    def test_it_falls_back_to_the_projects_default_environment(
        self, tmp_path, gcc_toolchain
    ):
        """The same environment `project.default_environment` names.

        One question, one answer: a target that names no environment and a
        caller reading the property must not land on different ones.
        """
        project = Project("p", root_dir=tmp_path)
        mcu, _host = _two_envs(project, gcc_toolchain)

        iface = project.HeaderOnlyLibrary("common")

        assert iface.env is mcu
        assert iface.env is project.default_environment

    def test_one_child_included_twice_makes_two_targets(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        envs = _two_envs(project, gcc_toolchain)

        made = [self._include(project, tmp_path, env) for env in envs]

        assert [t.qualified_name for t in made] == [
            "child::common@mcu",
            "child::common@host",
        ]
        assert project.get_target("common@host") is made[1]
