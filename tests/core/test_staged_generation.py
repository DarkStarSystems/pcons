# SPDX-License-Identifier: MIT
"""Configure dependencies, the regen edge, and staged generation."""

from pathlib import Path

import pytest

from pcons.core import invocation
from pcons.core.errors import PconsError
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator


@pytest.fixture
def recorded_invocation(tmp_path):
    """Record an invocation, as the CLI does, so a regen edge is emitted."""
    script = tmp_path / "pcons-build.py"
    script.write_text("# build script\n")
    invocation.record(invocation.Invocation(script=script))
    return script


class TestConfigureDependencies:
    def test_explicit_dependency_is_recorded(self, tmp_path):
        project = Project("p", root_dir=tmp_path)
        project.add_configure_dependency(tmp_path / "plugins.def")

        assert Path("plugins.def") in project.configure_dependencies

    def test_dependency_is_deduplicated(self, tmp_path):
        project = Project("p", root_dir=tmp_path)
        project.add_configure_dependency(tmp_path / "a.txt")
        project.add_configure_dependency("a.txt")

        assert project.configure_dependencies.count(Path("a.txt")) == 1

    def test_subproject_dependency_lands_on_top_level(self, tmp_path):
        top = Project("top", root_dir=tmp_path)
        (tmp_path / "sub").mkdir()
        sub = Project("sub", root_dir=tmp_path / "sub")

        sub.add_configure_dependency(tmp_path / "sub" / "data.txt")

        assert Path("sub/data.txt") in top.configure_dependencies


class TestRegenEdge:
    def test_ninja_emits_generator_edge(self, tmp_path, recorded_invocation):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "rule pcons_regen" in content
        assert "generator = 1" in content
        assert "build build.ninja: pcons_regen |" in content
        assert "$topdir/plugins.def" in content

    def test_build_tree_dependency_matches_its_producing_edge(
        self, tmp_path, recorded_invocation
    ):
        """A generated manifest must be named the way its own rule names it,
        or ninja can't connect the two."""
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(Path("build/gen/list.txt"))

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        regen_edge = next(
            line
            for line in content.splitlines()
            if line.startswith("build build.ninja")
        )
        assert " gen/list.txt" in regen_edge
        assert "$topdir/build/gen/list.txt" not in regen_edge

    def test_no_edge_without_a_reconstructable_invocation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["/usr/bin/pytest"])
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "pcons_regen" not in content

    def test_makefile_emits_remake_rule(self, tmp_path, recorded_invocation):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.add_configure_dependency(tmp_path / "plugins.def")

        MakefileGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "Makefile").read_text()
        assert "Makefile: " in content
        assert str(tmp_path / "plugins.def") in content


class TestGeneratedInput:
    def test_returns_none_and_registers_when_absent(self, tmp_path):
        project = Project("p", root_dir=tmp_path, build_dir="build")

        result = project.generated_input(Path("build/gen/list.txt"))

        assert result is None
        assert Path("build/gen/list.txt") in project.configure_dependencies

    def test_returns_path_when_present(self, tmp_path):
        gen = tmp_path / "build" / "gen"
        gen.mkdir(parents=True)
        (gen / "list.txt").write_text("one\n")
        project = Project("p", root_dir=tmp_path, build_dir="build")

        result = project.generated_input(Path("build/gen/list.txt"))

        assert result == gen / "list.txt"

    def test_when_generated_skips_the_block_until_the_input_exists(self, tmp_path):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        ran = []

        @project.when_generated(Path("build/gen/list.txt"))
        def _block(path):
            ran.append(path)

        assert ran == []

    def test_when_generated_runs_the_block_once_present(self, tmp_path):
        gen = tmp_path / "build" / "gen"
        gen.mkdir(parents=True)
        (gen / "list.txt").write_text("one\n")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        ran = []

        @project.when_generated(Path("build/gen/list.txt"))
        def _block(path):
            ran.append(path)

        assert ran == [gen / "list.txt"]

    def test_unproduced_staged_input_is_an_error(self, tmp_path, gcc_toolchain):
        """Waiting on a file no rule produces would wait forever."""
        project = Project("p", root_dir=tmp_path, build_dir="build")
        project.Environment(toolchain=gcc_toolchain)
        project.generated_input(Path("build/gen/list.txt"))

        with pytest.raises(PconsError, match="not produced by any build rule"):
            project.resolve()

    def test_staged_input_produced_by_a_command_is_accepted(
        self, tmp_path, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=Path("build/gen/list.txt"),
            source=None,
            command="generate $TARGET",
        )
        project.generated_input(Path("build/gen/list.txt"))

        project.resolve()  # does not raise
