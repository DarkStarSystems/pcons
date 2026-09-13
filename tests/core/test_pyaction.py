# SPDX-License-Identifier: MIT
"""Tests for env.PyAction(), the decorator that makes a function a build edge."""

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
from pcons.tools.pyaction import PyAction
from pcons.workers.python import PythonWorker
from pcons.workers.python_server import script_argv

RUNNER = Path("pcons/util/pyaction.py")


def make_project(tmp_path: Path) -> Project:
    """A project with one source file to work from."""
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    return Project("pyact", root_dir=tmp_path)


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


def one_source(project: Project, env: Any, **how: Any) -> Target:
    """One edge with one target and one source, resolved."""

    @env.PyAction(**how)
    def report(sources, targets):
        from pathlib import Path

        Path(targets[0]).write_text(Path(sources[0]).read_text())

    made = report(target="report.txt", source=["a.txt"])
    project.resolve()
    return made


class TestDecoration:
    def test_the_decorated_name_becomes_a_builder(
        self, project: Project, env: Any
    ) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        assert isinstance(report, PyAction)
        assert report.function.__name__ == "report"

    def test_the_builder_names_its_function(self, project: Project, env: Any) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        assert repr(report) == "<PyAction report>"

    def test_the_call_returns_the_target(self, project: Project, env: Any) -> None:
        made = one_source(project, env)

        assert isinstance(made, Target)
        assert made.name == "report"

    def test_one_decoration_makes_as_many_edges_as_it_is_called(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyAction()
        def report(sources, targets, n):
            return n

        made = [report(target=f"r{n}.txt", source=["a.txt"], n=n) for n in (1, 2, 3)]
        project.resolve()

        assert [t.name for t in made] == ["r1", "r2", "r3"]
        assert sorted(q.name for q in (tmp_path / "build" / "pyact").iterdir()) == [
            "r1.args.pkl",
            "r2.args.pkl",
            "r3.args.pkl",
            "report.py",
        ]

    def test_the_module_is_named_after_the_function_and_the_pickle_after_the_edge(
        self, project: Project, env: Any
    ) -> None:
        @env.PyAction()
        def whatever(sources, targets):
            return 1

        made = whatever(target="out.txt", source=["a.txt"])
        project.resolve()

        assert made.name == "out"
        assert node_tokens(made) == [
            "build/pyact/whatever.py",
            "build/pyact/out.args.pkl",
        ]

    def test_an_explicit_name_wins(self, project: Project, env: Any) -> None:
        @env.PyAction()
        def whatever(sources, targets):
            return 1

        made = whatever(target="out.txt", name="render", source=["a.txt"])

        assert made.name == "render"

    def test_the_call_takes_no_positional_arguments(
        self, project: Project, env: Any
    ) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        with pytest.raises(TypeError):
            report("out.txt")  # ty: ignore[too-many-positional-arguments]


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
            "build/pyact/report.py",
            "build/pyact/report.args.pkl",
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
            "build/pyact/report.py",
            "build/pyact/report.args.pkl",
        ]

    def test_the_sources_are_the_scripts_own(self, project: Project, env: Any) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        made = report(target="report.txt", source=["b.txt", "a.txt"])
        project.resolve()

        assert source_paths(made) == ["b.txt", "a.txt"]

    def test_no_source_leaves_an_empty_source_list(
        self, project: Project, env: Any
    ) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        made = report(target="report.txt")
        project.resolve()

        assert source_paths(made) == []
        assert tokens(made)[-1] == SourcePath()

    def test_two_targets_are_counted(self, project: Project, env: Any) -> None:
        @env.PyAction()
        def report(sources, targets):
            return 1

        made = report(target=["one.txt", "two.txt"], source=["a.txt"])
        project.resolve()

        assert tokens(made)[4:6] == ["--n-targets", "2"]

    def test_a_target_as_a_source_resolves_to_its_outputs(
        self, project: Project, env: Any
    ) -> None:
        first = env.Command(
            target="made.txt", source=["a.txt"], command=["cp", "$SOURCE", "$TARGET"]
        )

        @env.PyAction()
        def report(sources, targets):
            return 1

        made = report(target="report.txt", source=[first])
        project.resolve()

        assert source_paths(made) == ["build/made.txt"]

    def test_the_interpreter_can_be_chosen(self, project: Project, env: Any) -> None:
        report = one_source(project, env, python="/usr/bin/python3")

        assert tokens(report)[0] == "/usr/bin/python3"


class TestGeneratedNinja:
    def test_the_module_is_named_from_the_build_directory(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        one_source(project, env)
        text = ninja_text(project, tmp_path)

        assert "pyact/report.py" in text
        assert "build/pyact/report.py" not in text
        assert "$topdir/build/pyact" not in text
        assert "pyaction.py" in text

    def test_an_out_of_tree_build_directory_needs_no_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """The build directory is not under the source tree, a real layout."""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "a.txt").write_text("first\n", encoding="utf-8")
        build_dir = tmp_path / "obuild"
        project = Project("pyact", root_dir=source_dir, build_dir=build_dir)
        env = project.Environment()
        one_source(project, env)

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        text = (build_dir / "build.ninja").read_text(encoding="utf-8")

        assert "pyact/report.py" in text
        assert str(build_dir) not in text
        assert "$topdir/pyact" not in text

    def test_a_subdirectory_names_its_module_once(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The offset is applied to a node path once, not twice.

        A plain build-relative path handed to ``source=`` would come back as
        ``sub/build/sub/pyact/report.py``, and only in a subdirectory.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("sub\n", encoding="utf-8")
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            env = child.Environment()

            @env.PyAction()
            def report(sources, targets):
                return 1

            made = report(target="report.txt", source=["a.txt"])

        project.resolve()
        text = ninja_text(project, tmp_path)

        assert node_tokens(made) == [
            "build/sub/pyact/report.py",
            "build/sub/pyact/report.args.pkl",
        ]
        assert "sub/pyact/report.py" in text
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
        """The shape a PyAction usually has: it rewrites its output every run."""
        one_source(project, env, write_if_different=True)
        text = ninja_text(project, tmp_path)

        assert "restat = 1" in text
        assert "pcons.tools.stable_output --pre" in text
        assert "pcons.tools.stable_output --post" in text

    def test_depends_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """``depends`` is a call option: it says what this edge waits on."""

        @env.PyAction()
        def report(sources, targets):
            return 1

        made = report(target="report.txt", source=["a.txt"], depends=["b.txt"])
        project.resolve()

        assert "b.txt" in implicit_deps(made)


class TestDecorationOptionsReachEveryEdge:
    """How the function runs is decided once and holds for every call."""

    def test_restat_and_the_worker_reach_both_edges(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyAction(restat=True, worker=PythonWorker())
        def report(sources, targets, n):
            return n

        report(target="one.txt", source=["a.txt"], n=1)
        report(target="two.txt", source=["b.txt"], n=2)
        project.resolve()
        text = ninja_text(project, tmp_path)

        assert text.count("restat = 1") == 2
        assert text.count("workers/client.py") == 2

    def test_the_interpreter_reaches_both_edges(
        self, project: Project, env: Any
    ) -> None:
        @env.PyAction(python="/usr/bin/python3")
        def report(sources, targets):
            return 1

        made = [report(target=f"r{n}.txt", source=["a.txt"]) for n in (1, 2)]
        project.resolve()

        assert [tokens(t)[0] for t in made] == ["/usr/bin/python3"] * 2


class TestArgumentsFitTheSignature:
    def test_a_function_with_no_arguments_takes_none(
        self, project: Project, env: Any
    ) -> None:
        made = one_source(project, env)

        assert made.name == "report"

    def test_a_var_keyword_signature_accepts_anything(
        self, project: Project, env: Any
    ) -> None:
        """A function that declares **kwargs really does take every keyword."""

        @env.PyAction()
        def report(sources, targets, **rest):
            return rest

        made = report(target="out.txt", source=["a.txt"], whatever=1, anything=2)
        project.resolve()

        assert made.name == "out"


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


class TestTheCallDecidesTheSlice:
    def test_a_call_inside_a_subdirectory_writes_under_it(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The environment follows the decoration, the offset follows the call.

        ``anchor_target_paths`` reads ``Project.current()._node_offset``, so
        one action decorated at the top level and called in two places writes
        a module into each slice. Both edges run the same environment, which
        is the one that decorated the function.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("sub\n", encoding="utf-8")

        @env.PyAction()
        def report(sources, targets):
            return 1

        outside = report(target="outside.txt", source=["a.txt"])
        with project._enter_subdir("sub"):
            inside = report(target="inside.txt", source=["a.txt"])

        project.resolve()

        assert node_tokens(outside) == [
            "build/pyact/report.py",
            "build/pyact/outside.args.pkl",
        ]
        assert node_tokens(inside) == [
            "build/sub/pyact/report.py",
            "build/sub/pyact/inside.args.pkl",
        ]
        top = tmp_path / "build/pyact/report.py"
        under = tmp_path / "build/sub/pyact/report.py"
        assert top.is_file()
        assert under.is_file()
        assert top.read_bytes() == under.read_bytes()
        assert outside.output_nodes[0].path.as_posix() == "build/outside.txt"
        assert inside.output_nodes[0].path.as_posix() == "build/sub/inside.txt"


class TestMultipleEnvironments:
    def test_a_factory_serves_two_environments(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The idiom: one factory, one decoration per environment."""

        def make_report(env: Any, title: str) -> Target:
            @env.PyAction()
            def report(sources, targets, title):
                from pathlib import Path

                Path(targets[0]).write_text(title, encoding="utf-8")

            return report(target="report.txt", source=["a.txt"], title=title)

        host = project.Environment(name="host")
        host.build_prefix = "host"
        strict = project.Environment(name="strict")
        strict.build_prefix = "strict"

        made = [make_report(env, f"{env.name} report") for env in (host, strict)]
        project.resolve()
        text = ninja_text(project, tmp_path)

        assert [t.name for t in made] == ["report", "report"]
        assert node_tokens(made[0])[0] == "build/host/pyact/report.py"
        assert node_tokens(made[1])[0] == "build/strict/pyact/report.py"
        assert (tmp_path / "build/host/pyact/report.py").is_file()
        assert (tmp_path / "build/strict/pyact/report.py").is_file()
        assert "host/report.txt" in text
        assert "strict/report.txt" in text
