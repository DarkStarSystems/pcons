# SPDX-License-Identifier: MIT
"""A derived label is not a name: `Target.anonymous` and what follows from it.

The builders a script names targets for — Program, StaticLibrary — keep the
uniqueness rule. The ones that derive a label from an output path — Command,
Install, Tarfile, Test — do not, because the label says nothing about which
target is meant.
"""

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


@pytest.fixture
def project(tmp_path):
    return Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestAnonymousLabels:
    def test_two_commands_may_wear_one_label(self, project):
        """`foo.h` and `foo.c` share a stem, and that is not a conflict."""
        env = project.Environment()
        header = env.Command(target="foo.h", command="touch $TARGET")
        source = env.Command(target="foo.c", command="touch $TARGET")

        assert header.name == source.name == "foo"
        assert header.anonymous and source.anonymous
        assert header is not source
        assert [t.name for t in project.targets] == ["foo", "foo"]

    def test_both_commands_resolve_and_reach_the_build_file(self, project, tmp_path):
        """Two edges wearing one label are two edges."""
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")
        env.Command(target="foo.c", command="touch $TARGET")

        project.resolve()
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "build foo.h:" in content
        assert "build foo.c:" in content

    def test_two_installs_into_one_directory(self, project, tmp_path):
        """A directory exists to be filled from several places."""
        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()

        first = project.Install("dist", [tmp_path / "a.txt"])
        second = project.Install("dist", [tmp_path / "b.txt"])

        assert first.name == second.name == "install_dist"
        assert first is not second

    def test_a_label_may_repeat_a_name(self, project):
        """A command writing `app.map` beside the program `app` is fine."""
        env = project.Environment()
        program = project.Program("app", env, sources=["main.c"])
        command = env.Command(target="app.map", command="touch $TARGET")

        assert command.name == program.name == "app"
        assert project.get_target("app") is program


class TestNamesStayUnique:
    def test_duplicate_named_target_still_raises(self, project):
        env = project.Environment()
        project.Program("app", env, sources=["main.c"])

        with pytest.raises(ValueError, match="Target 'app' already exists"):
            project.StaticLibrary("app", env, sources=["lib.c"])

    def test_named_environments_still_part_two_names(self, project):
        host = project.Environment(name="host")
        mcu = project.Environment(name="mcu")

        project.Program("app", host, sources=["main.c"])
        project.Program("app", mcu, sources=["main.c"])

        assert project.get_target("app@host").env is host
        assert project.get_target("app@mcu").env is mcu


class TestLookupsIgnoreLabels:
    def test_get_target_does_not_find_a_command(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")

        with pytest.raises(KeyError) as excinfo:
            project.get_target("foo")
        message = str(excinfo.value)
        assert "Command derived that label" in message
        assert "project.Alias('foo', ...)" in message

    def test_has_target_is_false_for_a_label(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")

        assert not project.has_target("foo")
        assert project.get_target("foo", raise_if_missing=False) is None

    def test_default_by_name_refuses_a_label(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")

        with pytest.raises(KeyError, match="derived that label"):
            project.Default("foo")

    def test_an_alias_is_how_a_command_gets_a_name(self, project, tmp_path):
        env = project.Environment()
        command = env.Command(target="foo.h", command="touch $TARGET")
        project.Alias("header", command)

        project.resolve()
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "build header: phony" in content


def test_install_target_is_anonymous(project, tmp_path):
    (tmp_path / "a.txt").touch()
    assert project.Install("dist", [tmp_path / "a.txt"]).anonymous


def test_archive_target_is_anonymous(project):
    env = project.Environment()
    assert project.Tarfile(env, output="docs.tar.gz", sources=[]).anonymous


def test_program_is_named(project):
    env = project.Environment()
    assert not project.Program("app", env, sources=["main.c"]).anonymous


def test_test_target_is_anonymous(project):
    env = project.Environment()
    program = project.Program("check", env, sources=["main.c"])
    project.Test("unit", program)
    assert all(t.anonymous for t in project.targets if t.target_type == "test")


def test_two_tests_may_share_a_name(project):
    """Two suites named the same thing are two runs, not an error."""
    env = project.Environment()
    program = project.Program("check", env, sources=["main.c"])
    project.Test("unit", program, args=["--fast"])
    project.Test("unit", program, args=["--slow"])

    assert [t.name for t in project.targets if t.target_type == "test"] == [
        "test_unit",
        "test_unit",
    ]
