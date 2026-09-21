# SPDX-License-Identifier: MIT
"""One script reaching a target another script declared, by name.

A project split over ``add_subdirectory`` declares generators, installs and
archives in one script and uses them in another, and the way across is
``project.get_target("sub::some-generator@a")``. That is why ``name=`` is
optional-but-real on ``Command``, ``PyBuilder``, ``Install`` and the rest:
without it there is no way to name a per-environment generator declared in a
loop, and the pattern these tests pin would be lost.

The return value of ``add_subdirectory`` is the other way across, and is
covered here beside the lookups so the two stay comparable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.util.add_subdirectory import add_subdirectory

ENV_NAMES = ("a", "b", "c")

#: Declares one named generator per environment, in a loop, and exports them.
GENERATORS = """
from pcons.core.project import Project

project = Project('{name}')
envs = [e for e in project.top.environments if e.name in {envs!r}]
generators = {{
    env.name: env.Command(
        target=f'{name}-{{env.name}}.txt',
        command='touch $TARGET',
        name='some-generator',
    )
    for env in envs
}}
"""


def _make_subdir(parent: Path | Project, name: str, content: str) -> Path:
    """Create a subdirectory holding a pcons-build.py."""
    root = parent.root_dir if isinstance(parent, Project) else parent
    subdir = root / name
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "pcons-build.py").write_text(content)
    return subdir


@pytest.fixture
def parent(tmp_path: Path, gcc_toolchain) -> Project:
    """A project with three named environments, as a variant build has."""
    project = Project("top", root_dir=tmp_path, build_dir=tmp_path / "build")
    for name in ENV_NAMES:
        env = project.Environment(toolchain=gcc_toolchain, name=name)
        env.build_prefix = name
    return project


@pytest.fixture
def sub(parent: Project) -> Project:
    """``sub/`` included once, declaring ``some-generator`` in each."""
    _make_subdir(parent, "sub", GENERATORS.format(name="sub", envs=ENV_NAMES))
    add_subdirectory("sub")
    return parent


class TestLookupAcrossScripts:
    """The contributor's pattern: a loop over environments, found by name."""

    def test_the_qualified_spelling_finds_each_one(self, sub: Project) -> None:
        for name in ENV_NAMES:
            found = sub.get_target(f"sub::some-generator@{name}")
            assert found.env is not None and found.env.name == name

    def test_the_unqualified_spelling_finds_them_too(self, sub: Project) -> None:
        for name in ENV_NAMES:
            assert sub.get_target(f"some-generator@{name}") is sub.get_target(
                f"sub::some-generator@{name}"
            )

    def test_the_targets_are_named_not_anonymous(self, sub: Project) -> None:
        found = sub.get_target("some-generator@a")

        assert found.name == "some-generator"
        assert not found.anonymous

    def test_a_parent_target_depends_on_one(self, sub: Project) -> None:
        (sub.root_dir / "src").mkdir()
        (sub.root_dir / "src" / "main.c").write_text("int main(void) { return 0; }\n")
        env = sub.environments[0]
        generator = sub.get_target("sub::some-generator@a")
        app = sub.Program("app", env, sources=["src/main.c"])
        app.depends(generator)

        sub.resolve()

        assert generator in app.transitive_dependencies()

    def test_a_sibling_script_finds_the_first_siblings_command(
        self, sub: Project
    ) -> None:
        """The second subdirectory looks up what the first declared."""
        _make_subdir(
            sub,
            "consumer",
            "from pcons.core.project import Project\n"
            "project = Project('consumer')\n"
            "found = project.top.get_target('sub::some-generator@b')\n",
        )

        ns = add_subdirectory("consumer")

        assert ns.found is sub.get_target("sub::some-generator@b")


class TestTheReturnValueRoute:
    """The alternative: the child exports the targets, the parent reads them."""

    def test_the_parent_reads_the_childs_module_scope(self, parent: Project) -> None:
        _make_subdir(parent, "sub", GENERATORS.format(name="sub", envs=ENV_NAMES))

        ns = add_subdirectory("sub")

        assert ns.generators["a"] is parent.get_target("sub::some-generator@a")
        assert set(ns.generators) == set(ENV_NAMES)


class TestOtherNamedBuilders:
    """Install and Tarfile carry a name across a script boundary too."""

    def test_a_named_install_is_found_from_the_parent(self, parent: Project) -> None:
        (parent.root_dir / "a.txt").touch()
        _make_subdir(
            parent,
            "sub",
            "from pcons.core.project import Project\n"
            "project = Project('sub')\n"
            "project.Install('dist', ['../a.txt'], name='staged')\n",
        )

        add_subdirectory("sub")

        assert parent.get_target("sub::staged").name == "staged"

    def test_a_named_tarfile_is_found_from_the_parent(self, parent: Project) -> None:
        _make_subdir(
            parent,
            "sub",
            "from pcons.core.project import Project\n"
            "project = Project('sub')\n"
            "env = project.top.environments[0]\n"
            "project.Tarfile(env, output='docs.tar.gz', sources=[], name='docs')\n",
        )

        add_subdirectory("sub")

        found = parent.get_target("sub::docs@a")
        assert found.name == "docs"
        assert not found.anonymous


class TestTheCliTranslatesTheName:
    """`pcons build some-generator@a` reaches the edge's output path."""

    def test_a_spelling_becomes_the_edges_output_path(self, sub: Project) -> None:
        from pcons.cli import _route_targets

        sub.resolve()

        assert _route_targets([sub], ["some-generator@a"]) == [
            (sub, ["a/sub/sub-a.txt"])
        ]
