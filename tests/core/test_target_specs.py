# SPDX-License-Identifier: MIT
"""``project::target@env``: ``::`` selects the project, ``@`` the environment."""

from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.core.target import split_target_spec


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


class TestSplitTargetSpec:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("common", (None, "common", None)),
            ("common@mcu", (None, "common", "mcu")),
            ("sub::common", ("sub", "common", None)),
            ("sub::common@mcu", ("sub", "common", "mcu")),
        ],
    )
    def test_it_splits(self, spec, expected):
        assert split_target_spec(spec) == expected

    @pytest.mark.parametrize("spec", ["common@", "a@b@c", "a::b::c"])
    def test_malformed_specs_raise(self, spec):
        with pytest.raises(ValueError):
            split_target_spec(spec)


class TestLookup:
    def test_the_environment_selects_one(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        libs = [
            project.StaticLibrary("common", env, sources=[])
            for env in _two_envs(project, gcc_toolchain)
        ]

        assert project.get_target("common@mcu") is libs[0]
        assert project.get_target("common@host") is libs[1]
        assert project.get_target("p::common@host") is libs[1]

    def test_an_unknown_environment_names_the_others(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env)

        with pytest.raises(KeyError, match="host, mcu"):
            project.get_target("common@arm")

        assert project.get_target("common@arm", raise_if_missing=False) is None

    def test_default_accepts_the_spelling(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        for env in _two_envs(project, gcc_toolchain):
            project.StaticLibrary("common", env, sources=["src/common.c"])

        project.Default("common@mcu")
        project.resolve()

        assert [t.qualified_name for t in project.default_targets] == ["p::common@mcu"]

    def test_a_qualified_name_this_project_lacks(self, tmp_path, gcc_toolchain):
        """'p::gone' stops here: the qualifier already named the project."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        project.StaticLibrary("common", env)

        with pytest.raises(KeyError, match="p::gone"):
            project.get_target("p::gone")

        assert project.get_target("p::gone", raise_if_missing=False) is None


class TestLinkStrings:
    def test_a_string_is_always_a_raw_link_token(self, tmp_path, gcc_toolchain):
        """link() never reads a target name; get_target does that."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain, name="mcu")
        app = project.Program("app", env)

        app.link("m")

        assert app.public.link_libs == ["m"]

    def test_a_target_is_linked_by_looking_it_up(self, tmp_path, source, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env, _host = _two_envs(project, gcc_toolchain)
        lib = project.StaticLibrary("common", env, sources=["src/common.c"])
        app = project.Program("app", env)

        app.link(project.get_target("common@mcu"))

        assert app.public.link_libs == [lib]
