# SPDX-License-Identifier: MIT
"""Tests for env.PyCommand(), the decorator that makes a function a build edge."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pcons.core.project import Project
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.workers.python import PythonWorker
from pcons.workers.python_server import script_argv

RUNNER = Path("pcons/util/pycommand.py")


def make_project(tmp_path: Path) -> Project:
    """A project with one source file to work from."""
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    return Project("pycmd", root_dir=tmp_path)


def build_info(target: Target) -> Mapping[str, Any]:
    """What the builder recorded on the edge's first output."""
    info = target.output_nodes[0]._build_info
    assert info is not None
    return info


def tokens(target: Target) -> list[Any]:
    """The edge's command, as tokens with the path markers still in place."""
    return list(build_info(target)["command"])


def source_paths(target: Target) -> list[str]:
    """The edge's sources, as node paths."""
    return [node.path.as_posix() for node in build_info(target)["sources"]]


def node_tokens(target: Target) -> list[str]:
    """The command's node tokens, which Command records as project paths."""
    return [token.path for token in tokens(target) if isinstance(token, PathToken)]


def implicit_deps(target: Target) -> list[str]:
    """The edge's implicit dependencies, as node paths."""
    return [Path(node.name).as_posix() for node in target.output_nodes[0].implicit_deps]


def ninja_text(project: Project, tmp_path: Path) -> str:
    """Generate and read build.ninja."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return make_project(tmp_path)


@pytest.fixture
def env(project: Project) -> Any:
    return project.Environment()


def one_source(project: Project, env: Any, **extra: Any) -> Target:
    """One edge with one target and one source, resolved."""

    @env.PyCommand(target="report.txt", source=["a.txt"], **extra)
    def report(sources, targets):
        from pathlib import Path

        Path(targets[0]).write_text(Path(sources[0]).read_text())

    project.resolve()
    return report


class TestDecoration:
    def test_the_decorated_name_becomes_a_target(
        self, project: Project, env: Any
    ) -> None:
        report = one_source(project, env)

        assert isinstance(report, Target)
        assert report.name == "report"

    def test_the_name_comes_from_the_target_not_the_function(
        self, project: Project, env: Any
    ) -> None:
        @env.PyCommand(target="out.txt", source=["a.txt"])
        def whatever(sources, targets):
            return 1

        assert whatever.name == "out"

    def test_an_explicit_name_wins(self, project: Project, env: Any) -> None:
        @env.PyCommand(target="out.txt", name="render", source=["a.txt"])
        def whatever(sources, targets):
            return 1

        assert whatever.name == "render"


class TestCommandShape:
    def test_the_runner_is_named_as_a_script(self, project: Project, env: Any) -> None:
        command = tokens(one_source(project, env))

        assert command[0] == sys.executable.replace("\\", "/")
        assert command[1].endswith(RUNNER.as_posix())
        assert "-m" not in command

    def test_the_generated_files_are_node_tokens(
        self, project: Project, env: Any
    ) -> None:
        report = one_source(project, env)
        command = tokens(report)

        assert node_tokens(report) == [
            "build/pycmd/report.py",
            "build/pycmd/report.args.pkl",
        ]
        assert command[4:6] == ["--n-targets", "1"]
        assert command[6] == TargetPath()
        assert command[7] == SourcePath()

    def test_the_generated_files_are_implicit_dependencies(
        self, project: Project, env: Any
    ) -> None:
        """A node token is not a source, it is what the edge waits on."""
        report = one_source(project, env)

        assert implicit_deps(report) == [
            "build/pycmd/report.py",
            "build/pycmd/report.args.pkl",
        ]

    def test_the_sources_are_the_scripts_own(self, project: Project, env: Any) -> None:
        @env.PyCommand(target="report.txt", source=["b.txt", "a.txt"])
        def report(sources, targets):
            return 1

        project.resolve()

        assert source_paths(report) == ["b.txt", "a.txt"]

    def test_no_source_leaves_an_empty_source_list(
        self, project: Project, env: Any
    ) -> None:
        @env.PyCommand(target="report.txt")
        def report(sources, targets):
            return 1

        project.resolve()

        assert source_paths(report) == []
        assert tokens(report)[-1] == SourcePath()

    def test_two_targets_are_counted(self, project: Project, env: Any) -> None:
        @env.PyCommand(target=["one.txt", "two.txt"], source=["a.txt"])
        def report(sources, targets):
            return 1

        project.resolve()

        assert tokens(report)[4:6] == ["--n-targets", "2"]

    def test_a_target_as_a_source_resolves_to_its_outputs(
        self, project: Project, env: Any
    ) -> None:
        first = env.Command(
            target="made.txt", source=["a.txt"], command=["cp", "$SOURCE", "$TARGET"]
        )

        @env.PyCommand(target="report.txt", source=[first])
        def report(sources, targets):
            return 1

        project.resolve()

        assert source_paths(report) == ["build/made.txt"]

    def test_the_interpreter_can_be_chosen(self, project: Project, env: Any) -> None:
        report = one_source(project, env, python="/usr/bin/python3")

        assert tokens(report)[0] == "/usr/bin/python3"


class TestGeneratedNinja:
    def test_the_module_is_named_from_the_build_directory(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        one_source(project, env)
        text = ninja_text(project, tmp_path)

        assert "pycmd/report.py" in text
        assert "build/pycmd/report.py" not in text
        assert "$topdir/build/pycmd" not in text
        assert "pycommand.py" in text

    def test_an_out_of_tree_build_directory_needs_no_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """The build directory is not under the source tree, a real layout."""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "a.txt").write_text("first\n", encoding="utf-8")
        build_dir = tmp_path / "obuild"
        project = Project("pycmd", root_dir=source_dir, build_dir=build_dir)
        env = project.Environment()
        one_source(project, env)

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        text = (build_dir / "build.ninja").read_text(encoding="utf-8")

        assert "pycmd/report.py" in text
        assert str(build_dir) not in text
        assert "$topdir/pycmd" not in text

    def test_a_subdirectory_names_its_module_once(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The offset is applied to a node path once, not twice.

        A plain build-relative path handed to ``source=`` would come back as
        ``sub/build/sub/pycmd/report.py``, and only in a subdirectory.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("sub\n", encoding="utf-8")
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            env = child.Environment()

            @env.PyCommand(target="report.txt", source=["a.txt"])
            def report(sources, targets):
                return 1

        project.resolve()
        text = ninja_text(project, tmp_path)

        assert node_tokens(report) == [
            "build/sub/pycmd/report.py",
            "build/sub/pycmd/report.args.pkl",
        ]
        assert "sub/pycmd/report.py" in text
        assert "sub/build" not in text

    def test_restat_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        one_source(project, env, restat=True)

        assert "restat = 1" in ninja_text(project, tmp_path)

    def test_env_vars_reach_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        one_source(project, env, env_vars={"REPORT_TITLE": "hello"})

        assert "REPORT_TITLE=hello" in ninja_text(project, tmp_path)

    def test_cwd_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "w"
        elsewhere.mkdir()
        one_source(project, env, cwd=elsewhere)
        text = ninja_text(project, tmp_path)

        assert "cd ../w &&" in text
        assert "&& cd ../build" in text

    def test_write_if_different_wraps_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The shape a PyCommand usually has: it rewrites its output every run."""
        one_source(project, env, write_if_different=True)
        text = ninja_text(project, tmp_path)

        assert "restat = 1" in text
        assert "pcons.tools.stable_output --pre" in text
        assert "pcons.tools.stable_output --post" in text

    def test_depends_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        report = one_source(project, env, depends=["b.txt"])

        assert "b.txt" in implicit_deps(report)


class TestWorker:
    def test_the_command_is_one_a_worker_can_run(
        self, project: Project, env: Any
    ) -> None:
        """``script_argv`` refuses ``-m``, so this is what makes worker= work."""
        report = one_source(project, env, worker=PythonWorker())
        argv = [token for token in tokens(report) if isinstance(token, str)]

        assert script_argv(argv) == argv[1:]

    def test_the_worker_launcher_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        one_source(project, env, worker=PythonWorker())

        assert "workers/client.py" in ninja_text(project, tmp_path)


class TestMultipleEnvironments:
    def test_a_factory_serves_two_environments(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The idiom: one factory, one decoration per environment."""

        def make_report(env: Any, title: str) -> Target:
            @env.PyCommand(
                target="report.txt", source=["a.txt"], kwargs={"title": title}
            )
            def report(sources, targets, title):
                from pathlib import Path

                Path(targets[0]).write_text(title, encoding="utf-8")

            return report

        host = project.Environment(name="host")
        host.build_prefix = "host"
        strict = project.Environment(name="strict")
        strict.build_prefix = "strict"

        made = [make_report(env, f"{env.name} report") for env in (host, strict)]
        project.resolve()
        text = ninja_text(project, tmp_path)

        assert [t.name for t in made] == ["report", "report"]
        assert node_tokens(made[0])[0] == "build/host/pycmd/report.py"
        assert node_tokens(made[1])[0] == "build/strict/pycmd/report.py"
        assert (tmp_path / "build/host/pycmd/report.py").is_file()
        assert (tmp_path / "build/strict/pycmd/report.py").is_file()
        assert "host/report.txt" in text
        assert "strict/report.txt" in text
