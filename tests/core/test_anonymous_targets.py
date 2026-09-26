# SPDX-License-Identifier: MIT
"""A derived label is not a name: `Target.anonymous` and what follows from it.

The builders a script names targets for — Program, StaticLibrary — keep the
uniqueness rule. The ones that derive a label — Command, Install, Tarfile,
Test — do not, because the label says nothing about which target is meant.
Most of those take an optional ``name=``, and a call that gives one gets a
named target back, with the same identity a Program's name carries.
"""

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


@pytest.fixture
def project(tmp_path):
    return Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestAnonymousLabels:
    def test_a_label_is_the_file_the_command_builds(self, project):
        """As the build tool writes it, so a reader can match the two."""
        env = project.Environment()
        made = env.Command(target="out/foo.h", command="touch $TARGET")

        assert made.name == "out/foo.h"
        assert made.anonymous

    def test_two_commands_of_one_stem_wear_different_labels(self, project):
        """`foo.h` and `foo.c` are different files, so they read differently."""
        env = project.Environment()
        header = env.Command(target="foo.h", command="touch $TARGET")
        source = env.Command(target="foo.c", command="touch $TARGET")

        assert header.name == "foo.h"
        assert source.name == "foo.c"
        assert header.anonymous and source.anonymous
        assert [t.name for t in project.targets] == ["foo.h", "foo.c"]

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
        assert [t.name for t in project.targets] == ["install_dist", "install_dist"]

    def test_a_label_may_repeat_a_name(self, project):
        """A command writing the program `app` again is fine."""
        env = project.Environment()
        program = project.Program("app", env, sources=["main.c"])
        command = env.Command(target="app", command="touch $TARGET")

        assert command.name == program.name == "app"
        assert project.get_target("app") is program


class TestNamesStayUnique:
    def test_duplicate_named_target_still_raises(self, project):
        env = project.Environment()
        project.Program("app", env, sources=["main.c"])

        with pytest.raises(
            ValueError, match=r"Target 'app' \(program\) already exists"
        ):
            project.Program("app", env, sources=["other.c"])

    def test_two_kinds_of_target_may_share_a_name(self, project):
        """They write `app` and `libapp.a`, and compile into their own
        object directories (see tests/core/test_object_dirs.py)."""
        env = project.Environment()
        program = project.Program("app", env, sources=["main.c"])
        library = project.StaticLibrary("app", env, sources=["lib.c"])

        assert project.targets == [program, library]

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

        with pytest.raises(KeyError, match="Target 'foo.h' not found"):
            project.get_target("foo.h")

    def test_has_target_is_false_for_a_label(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")

        assert not project.has_target("foo.h")
        assert project.get_target("foo.h", raise_if_missing=False) is None

    def test_default_by_name_refuses_a_label(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET")

        with pytest.raises(KeyError, match="is not a known alias or target"):
            project.Default("foo.h")

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
        "unit",
        "unit",
    ]


def test_a_test_name_is_written_for_a_person(project):
    """A label reaches no path, so a sentence with spaces is a fine test name."""
    env = project.Environment()
    program = project.Program("check", env, sources=["main.c"])

    assert project.Test("server connects", program).name == "server connects"


class TestANameMakesTheTargetNamed:
    """`name=` is optional on these builders, and giving one is an identity."""

    def test_a_named_command_is_not_anonymous(self, project):
        env = project.Environment()
        made = env.Command(target="foo.h", command="touch $TARGET", name="gen")

        assert made.name == "gen"
        assert not made.anonymous

    def test_get_target_finds_it(self, project):
        env = project.Environment()
        made = env.Command(target="foo.h", command="touch $TARGET", name="gen")

        assert project.get_target("gen") is made

    def test_a_second_command_of_that_name_raises(self, project):
        env = project.Environment()
        env.Command(target="foo.h", command="touch $TARGET", name="gen")

        with pytest.raises(
            ValueError, match=r"Target 'gen' \(command\) already exists"
        ):
            env.Command(target="foo.c", command="touch $TARGET", name="gen")

    def test_two_environments_part_one_name(self, project):
        a = project.Environment(name="a")
        b = project.Environment(name="b")

        first = a.Command(target="a/foo.h", command="touch $TARGET", name="gen")
        second = b.Command(target="b/foo.h", command="touch $TARGET", name="gen")

        assert project.get_target("gen@a") is first
        assert project.get_target("gen@b") is second

    def test_a_named_install_is_found_by_name(self, project, tmp_path):
        (tmp_path / "a.txt").touch()
        made = project.Install("dist", [tmp_path / "a.txt"], name="staged")

        assert not made.anonymous
        assert project.get_target("staged") is made

    def test_a_name_goes_through_the_character_rule(self, project):
        env = project.Environment()

        with pytest.raises(ValueError, match="invalid characters"):
            env.Command(target="foo.h", command="touch $TARGET", name="gen it")
