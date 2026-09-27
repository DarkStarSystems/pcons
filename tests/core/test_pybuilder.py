# SPDX-License-Identifier: MIT
"""Tests for env.PyBuilder(), the decorator that makes a function a build edge."""

from __future__ import annotations

import inspect
import os
import pickle
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pcons.configure.platform import get_platform
from pcons.core.errors import PconsError
from pcons.core.project import Project
from pcons.core.subst import PathToken, ProjectPath, SourcePath, Subst, TargetPath
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.tools import pybuilder as pybuilder_module
from pcons.tools.pybuilder import PyBuilder, PyBuilderError
from pcons.workers.python import PythonWorker
from pcons.workers.python_server import script_argv
from tests.support import REPO_ROOT, subprocess_env

RUNNER = "build/pybuilder/pcons-runner/pcons-runner.py"


def make_project(tmp_path: Path) -> Project:
    """A project with one source file to work from."""
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    return Project("wordcount", root_dir=tmp_path)


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
    return [
        Path(token.path).as_posix()
        for token in tokens(target)
        if isinstance(token, PathToken)
    ]


def implicit_deps(target: Target) -> list[str]:
    """The edge's implicit dependencies, as node paths."""
    return [Path(node.name).as_posix() for node in target.output_nodes[0].implicit_deps]


def payload_of(target: Target, tmp_path: Path) -> dict[str, Any]:
    """Unpickle the sidecar argument pickle a resolved edge wrote."""
    return pickle.loads((tmp_path / node_tokens(target)[2]).read_bytes())


def ninja_text(project: Project, tmp_path: Path) -> str:
    """Generate and read build.ninja, with every path spelled one way.

    These tests ask which files an edge names, never how the generator spells
    a separator, and on Windows it writes backslashes.
    """
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    text = (tmp_path / "build" / "build.ninja").read_text(encoding="utf-8")
    return text.replace("\\", "/")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return make_project(tmp_path)


@pytest.fixture
def env(project: Project) -> Any:
    return project.Environment()


def one_source(project: Project, env: Any, **how: Any) -> Target:
    """One edge with one target and one source, resolved."""

    @env.PyBuilder(**how)
    def report(targets, sources):
        from pathlib import Path

        Path(targets[0]).write_text(Path(sources[0]).read_text())

    made = report(target="report.txt", source=["a.txt"])
    project.resolve()
    return made


class TestDecoration:
    def test_the_decorated_name_becomes_a_builder(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        assert isinstance(report, PyBuilder)
        assert report.function.__name__ == "report"

    def test_the_builder_names_its_function(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        assert repr(report) == "<PyBuilder report>"

    def test_the_call_returns_the_target(self, project: Project, env: Any) -> None:
        made = one_source(project, env)

        assert isinstance(made, Target)
        assert made.name == "report.txt"

    def test_one_decoration_makes_as_many_edges_as_it_is_called(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources, n):
            return n

        made = [report(target=f"r{n}.txt", source=["a.txt"], n=n) for n in (1, 2, 3)]
        project.resolve()

        assert [t.name for t in made] == ["r1.txt", "r2.txt", "r3.txt"]
        assert sorted(q.name for q in (tmp_path / "build" / "pybuilder").iterdir()) == [
            "pcons-runner",
            "r1.txt.args.pkl",
            "r2.txt.args.pkl",
            "r3.txt.args.pkl",
            "report.py",
        ]

    def test_the_module_is_named_after_the_function_and_the_pickle_after_the_target(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def whatever(targets, sources):
            return 1

        made = whatever(target="out.txt", source=["a.txt"])
        project.resolve()

        assert made.name == "out.txt"
        assert node_tokens(made) == [
            RUNNER,
            "build/pybuilder/whatever.py",
            "build/pybuilder/out.txt.args.pkl",
        ]

    def test_the_call_takes_no_positional_arguments(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        with pytest.raises(TypeError):
            report("out.txt")  # ty: ignore[too-many-positional-arguments]


class TestTheEdgeMayBeNamed:
    """`name=` reaches the Command the edge becomes, and is an identity."""

    def _builder(self, env: Any) -> Any:
        @env.PyBuilder()
        def report(targets, sources):
            from pathlib import Path

            Path(targets[0]).write_text(Path(sources[0]).read_text())

        return report

    def test_an_unnamed_edge_is_anonymous(self, project: Project, env: Any) -> None:
        made = self._builder(env)(target="report.txt", source=["a.txt"])

        assert made.anonymous
        assert made.name == "report.txt"

    def test_a_named_edge_is_found_by_name(self, project: Project, env: Any) -> None:
        made = self._builder(env)(
            target="report.txt", source=["a.txt"], name="report-gen"
        )

        assert not made.anonymous
        assert project.get_target("report-gen") is made

    def test_the_name_is_reserved_in_the_function(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="name"):

            @env.PyBuilder()
            def report(targets, sources, name):
                pass


class TestCommandShape:
    def test_the_runner_is_named_as_a_script(self, project: Project, env: Any) -> None:
        command = tokens(one_source(project, env))

        assert command[0] == sys.executable.replace("\\", "/")
        assert isinstance(command[1], PathToken)
        assert Path(command[1].path).as_posix() == RUNNER
        assert "-m" not in command

    def test_the_generated_files_are_node_tokens(
        self, project: Project, env: Any
    ) -> None:
        report = one_source(project, env)
        command = tokens(report)

        assert node_tokens(report) == [
            RUNNER,
            "build/pybuilder/report.py",
            "build/pybuilder/report.txt.args.pkl",
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
            RUNNER,
            "build/pybuilder/report.py",
            "build/pybuilder/report.txt.args.pkl",
        ]

    def test_the_sources_are_the_scripts_own(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["b.txt", "a.txt"])
        project.resolve()

        assert source_paths(made) == ["b.txt", "a.txt"]

    def test_no_source_leaves_an_empty_source_list(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt")
        project.resolve()

        assert source_paths(made) == []
        assert tokens(made)[-1] == SourcePath()

    def test_two_targets_are_counted(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
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

        @env.PyBuilder()
        def report(targets, sources):
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

        assert "pybuilder/report.py" in text
        assert "build/pybuilder/report.py" not in text
        assert "$topdir/build/pybuilder" not in text
        assert " pybuilder/pcons-runner/pcons-runner.py pybuilder/report.py " in text

    def test_an_out_of_tree_build_directory_needs_no_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """The build directory is not under the source tree, a real layout."""
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "a.txt").write_text("first\n", encoding="utf-8")
        build_dir = tmp_path / "obuild"
        project = Project("wordcount", root_dir=source_dir, build_dir=build_dir)
        env = project.Environment()
        one_source(project, env)

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        text = (build_dir / "build.ninja").read_text(encoding="utf-8")
        spelled = text.replace("\\", "/")

        assert "pybuilder/report.py" in spelled
        assert build_dir.as_posix() not in spelled
        assert "$topdir/pybuilder" not in spelled

    def test_a_subdirectory_names_its_module_once(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The offset is applied to a node path once, not twice.

        A plain build-relative path handed to ``source=`` would come back as
        ``sub/build/sub/pybuilder/report.py``, and only in a subdirectory.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("sub\n", encoding="utf-8")
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            env = child.Environment()

            @env.PyBuilder()
            def report(targets, sources):
                return 1

            made = report(target="report.txt", source=["a.txt"])

        project.resolve()
        text = ninja_text(project, tmp_path)

        assert node_tokens(made) == [
            RUNNER,
            "build/sub/pybuilder/report.py",
            "build/sub/pybuilder/report.txt.args.pkl",
        ]
        assert "sub/pybuilder/report.py" in text
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

        cd = "cd /d" if get_platform().is_windows else "cd"
        build = tmp_path / "build"
        there = Path(os.path.relpath(elsewhere, build)).as_posix()
        back = Path(os.path.relpath(build, elsewhere)).as_posix()
        runner = Path(os.path.relpath(tmp_path / RUNNER, elsewhere)).as_posix()
        assert f"{cd} {there} &&" in text
        assert f"&& {cd} {back}" in text
        assert re.search(f'[ "]{re.escape(runner)}[ "]', text)

    def test_write_if_different_wraps_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The shape a PyBuilder usually has: it rewrites its output every run."""
        one_source(project, env, write_if_different=True)
        text = ninja_text(project, tmp_path)

        assert "restat = 1" in text
        assert "pcons.tools.stable_output --pre" in text
        assert "pcons.tools.stable_output --post" in text

    def test_depends_reaches_the_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """``depends`` is a call option: it says what this edge waits on."""

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"], depends=["b.txt"])
        project.resolve()

        assert "b.txt" in implicit_deps(made)


class TestDecorationOptionsReachEveryEdge:
    """How the function runs is decided once and holds for every call."""

    def test_restat_and_the_worker_reach_both_edges(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(restat=True, worker=PythonWorker())
        def report(targets, sources, n):
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
        @env.PyBuilder(python="/usr/bin/python3")
        def report(targets, sources):
            return 1

        made = [report(target=f"r{n}.txt", source=["a.txt"]) for n in (1, 2)]
        project.resolve()

        assert [tokens(t)[0] for t in made] == ["/usr/bin/python3"] * 2

    def test_decoration_depends_reaches_every_edge(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(depends=["b.txt"])
        def report(targets, sources):
            return 1

        report(target="one.txt", source=["a.txt"])
        report(target="two.txt", source=["a.txt"])
        project.resolve()
        text = ninja_text(project, tmp_path)

        assert text.count("b.txt") == 2


class TestBuilderDepends:
    """``PyBuilder.depends()``: a builder-level dependency added after decoration."""

    def test_before_any_call_reaches_the_edge(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        report.depends("b.txt")
        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert "b.txt" in implicit_deps(made)

    def test_after_two_calls_reaches_both(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        first = report(target="one.txt", source=["a.txt"])
        second = report(target="two.txt", source=["a.txt"])
        report.depends("b.txt")
        project.resolve()

        assert "b.txt" in implicit_deps(first)
        assert "b.txt" in implicit_deps(second)

    def test_call_depends_adds_to_the_builders(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder(depends=["b.txt"])
        def report(targets, sources):
            return 1

        report.depends("c.txt")
        made = report(target="report.txt", source=["a.txt"], depends=["d.txt"])
        project.resolve()

        deps = implicit_deps(made)
        assert "b.txt" in deps
        assert "c.txt" in deps
        assert "d.txt" in deps

    def test_returns_the_builder_for_chaining(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        assert report.depends("b.txt") is report

    def test_after_resolve_raises(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        report(target="report.txt", source=["a.txt"])
        project.resolve()

        with pytest.raises(RuntimeError):
            report.depends("b.txt")

    def test_a_relative_string_in_a_subdirectory_resolves_like_command_depends(
        self, tmp_path: Path
    ) -> None:
        """A builder-level item goes through ``Target.depends``, the same as
        a call's, so a relative string reads the same directory either way.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("first\n", encoding="utf-8")
        (tmp_path / "sub" / "b.txt").write_text("second\n", encoding="utf-8")
        project = Project("wordcount", root_dir=tmp_path)
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            sub_env: Any = child.Environment()

            @sub_env.PyBuilder(depends=["b.txt"])
            def report(targets, sources):
                return 1

            py_made = report(target="report.txt", source=["a.txt"])
            cmd_made = sub_env.Command(
                target="command.txt",
                source=["a.txt"],
                command="true",
                depends=["b.txt"],
            )

        project.resolve()

        assert "sub/b.txt" in implicit_deps(py_made)
        assert implicit_deps(cmd_made) == ["sub/b.txt"]


class TestArgumentsFitTheSignature:
    def test_a_function_with_no_arguments_takes_none(
        self, project: Project, env: Any
    ) -> None:
        made = one_source(project, env)

        assert made.name == "report.txt"

    def test_a_var_keyword_signature_accepts_anything(
        self, project: Project, env: Any
    ) -> None:
        """A function that declares **kwargs really does take every keyword."""

        @env.PyBuilder()
        def report(targets, sources, **rest):
            return rest

        made = report(target="out.txt", source=["a.txt"], whatever=1, anything=2)
        project.resolve()

        assert made.name == "out.txt"


class TestWorker:
    def test_the_command_is_one_a_worker_can_run(
        self, project: Project, env: Any
    ) -> None:
        """``script_argv`` refuses ``-m``, so this is what makes worker= work."""
        report = one_source(project, env, worker=PythonWorker())
        argv = [
            Path(token.path).as_posix() if isinstance(token, PathToken) else token
            for token in tokens(report)
            if isinstance(token, (str, PathToken))
        ]

        assert argv[1] == RUNNER
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
        one builder decorated at the top level and called in two places writes
        a module into each slice. Both edges run the same environment, which
        is the one that decorated the function.
        """
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("sub\n", encoding="utf-8")

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        outside = report(target="outside.txt", source=["a.txt"])
        with project._enter_subdir("sub"):
            inside = report(target="inside.txt", source=["a.txt"])

        project.resolve()

        assert node_tokens(outside) == [
            RUNNER,
            "build/pybuilder/report.py",
            "build/pybuilder/outside.txt.args.pkl",
        ]
        assert node_tokens(inside) == [
            RUNNER,
            "build/sub/pybuilder/report.py",
            "build/sub/pybuilder/inside.txt.args.pkl",
        ]
        top = tmp_path / "build/pybuilder/report.py"
        under = tmp_path / "build/sub/pybuilder/report.py"
        assert top.is_file()
        assert under.is_file()
        assert top.read_bytes() == under.read_bytes()
        assert outside.output_nodes[0].path.as_posix() == "build/outside.txt"
        assert inside.output_nodes[0].path.as_posix() == "build/sub/inside.txt"
        assert outside.env is inside.env is env


class TestMultipleEnvironments:
    def test_a_factory_serves_two_environments(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The idiom: one factory, one decoration per environment."""

        def make_report(env: Any, title: str) -> Target:
            @env.PyBuilder()
            def report(targets, sources, title):
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

        assert [t.name for t in made] == ["host/report.txt", "strict/report.txt"]
        assert node_tokens(made[0]) == [
            RUNNER,
            "build/host/pybuilder/report.py",
            "build/host/pybuilder/report.txt.args.pkl",
        ]
        assert node_tokens(made[1]) == [
            RUNNER,
            "build/strict/pybuilder/report.py",
            "build/strict/pybuilder/report.txt.args.pkl",
        ]
        assert (tmp_path / "build/host/pybuilder/report.py").is_file()
        assert (tmp_path / "build/strict/pybuilder/report.py").is_file()
        assert "host/report.txt" in text
        assert "strict/report.txt" in text

    def test_two_named_environments_sharing_a_build_directory_write_distinct_pickles(
        self, project: Project
    ) -> None:
        """Neither environment has a build_prefix, so both share one gen
        dir. The pickle follows the target, so the two edges land on
        distinct files; before it did, both wanted
        ``build/pybuilder/report.args.pkl`` and the second call raised."""
        one = project.Environment(name="one")
        two = project.Environment(name="two")

        @one.PyBuilder()
        def render_one(targets, sources):
            return 1

        @two.PyBuilder()
        def render_two(targets, sources):
            return 1

        first = render_one(target="one/report.txt", source=["a.txt"])
        second = render_two(target="two/report.txt", source=["a.txt"])
        project.resolve()

        assert (first.name, second.name) == ("one/report.txt", "two/report.txt")
        assert node_tokens(first)[2] == "build/pybuilder/one/report.txt.args.pkl"
        assert node_tokens(second)[2] == "build/pybuilder/two/report.txt.args.pkl"


class TestFilesAppearAtResolve:
    """A build description writes nothing until it is resolved."""

    def test_running_an_example_by_hand_writes_nothing(self, tmp_path: Path) -> None:
        """``python pcons-build.py`` describes the build and exits."""
        example = tmp_path / "example"
        shutil.copytree(
            REPO_ROOT / "examples" / "90_python_builder",
            example,
            ignore=shutil.ignore_patterns(
                "build", "compile_commands.json", "__pycache__"
            ),
        )

        result = subprocess.run(
            [sys.executable, "pcons-build.py"],
            cwd=example,
            capture_output=True,
            text=True,
            timeout=120,
            env=subprocess_env(),
        )

        assert result.returncode == 0, result.stderr
        assert "this build script was run directly" in result.stderr
        assert not (example / "build").exists()

    def test_the_call_writes_nothing_and_resolve_writes_both(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources, title):
            return title

        made = report(target="report.txt", source=["a.txt"], title="counts")

        assert not (tmp_path / "build").exists()

        project.resolve()
        runner, module, args = (tmp_path / token for token in node_tokens(made))

        assert module.read_text(encoding="utf-8").endswith(
            "def report(targets, sources, title):\n    return title\n"
        )
        assert args.is_file()
        assert runner.is_file()

    def test_a_second_run_over_the_same_description_keeps_both_mtimes(
        self, tmp_path: Path
    ) -> None:
        def describe() -> list[Path]:
            project = make_project(tmp_path)
            env = project.Environment()

            @env.PyBuilder()
            def report(targets, sources):
                return 1

            made = report(target="report.txt", source=["a.txt"])
            project.resolve()
            return [tmp_path / token for token in node_tokens(made)]

        written = describe()
        for path in written:
            os.utime(path, (0, 0))

        Project._clear_tree()
        again = describe()

        assert again == written
        assert [path.stat().st_mtime for path in written] == [0, 0, 0]

    def test_resolving_twice_keeps_both_mtimes(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        made = one_source(project, env)
        written = [tmp_path / token for token in node_tokens(made)]
        for path in written:
            os.utime(path, (0, 0))

        project.resolve()

        assert [path.stat().st_mtime for path in written] == [0, 0, 0]

    def test_an_edge_sharing_a_module_still_writes_its_own_pickle(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return 1

        first = report(target="one.txt", source=["a.txt"])
        second = report(target="two.txt", source=["a.txt"])
        project.resolve()

        assert node_tokens(first)[:2] == node_tokens(second)[:2]
        assert all(
            (tmp_path / token).is_file()
            for token in (*node_tokens(first), *node_tokens(second))
        )

    def test_a_refused_call_leaves_nothing_to_write(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources, handle):
            return handle

        with pytest.raises(PyBuilderError, match="cannot pickle"):
            report(target="report.txt", source=["a.txt"], handle=lambda: None)
        project.resolve()

        assert not (tmp_path / "build" / "pybuilder").exists()


class TestSysPath:
    """The pickle carries the ``sys.path`` the decorating script had."""

    def test_an_entry_present_before_decoration_is_stored(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "path", list(sys.path))
        before = tmp_path / "before"
        sys.path.append(str(before))

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert before.as_posix() in payload_of(made, tmp_path)["path"]

    def test_an_entry_appended_after_decoration_is_not_stored(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "path", list(sys.path))

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        after = tmp_path / "after"
        sys.path.append(str(after))
        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert after.as_posix() not in payload_of(made, tmp_path)["path"]

    def test_order_and_duplicates_survive(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        marker = tmp_path / "marker"
        monkeypatch.setattr(sys, "path", [str(marker), str(marker), "."])

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [
            marker.as_posix(),
            marker.as_posix(),
            Path.cwd().as_posix(),
        ]

    def test_every_entry_is_absolute(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "path", list(sys.path))

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert all(
            Path(entry).is_absolute() for entry in payload_of(made, tmp_path)["path"]
        )

    def test_an_explicit_interpreter_stores_no_path(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(python=sys.executable)
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] is None

    def test_the_launcher_entry_is_left_out(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = tmp_path / "launcher"
        monkeypatch.setattr(sys, "path", [str(launcher), str(tmp_path)])
        monkeypatch.setattr(
            pybuilder_module, "launcher_entry", lambda: launcher.as_posix()
        )

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [tmp_path.as_posix()]

    def test_a_duplicate_of_the_launcher_entry_later_stays(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = tmp_path / "launcher"
        monkeypatch.setattr(sys, "path", [str(launcher), str(launcher)])
        monkeypatch.setattr(
            pybuilder_module, "launcher_entry", lambda: launcher.as_posix()
        )

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [launcher.as_posix()]

    def test_a_launcher_entry_spelled_with_the_other_separator_is_left_out(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`sys.path` and the recorded launcher entry may spell the same
        path with a different separator (POSIX vs. native, on Windows).
        The other tests here always put the native spelling in `sys.path`
        and the POSIX one in the mocked `launcher_entry`; this reverses
        which side spells it which way, so the match stays proven either
        way round.
        """
        launcher = tmp_path / "launcher"
        monkeypatch.setattr(sys, "path", [launcher.as_posix(), str(tmp_path)])
        monkeypatch.setattr(pybuilder_module, "launcher_entry", lambda: str(launcher))

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [tmp_path.as_posix()]

    @pytest.mark.skipif(
        sys.platform != "win32", reason="paths are case-insensitive on Windows only"
    )
    def test_a_launcher_entry_differing_only_in_case_is_left_out(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        launcher = tmp_path / "launcher"
        monkeypatch.setattr(sys, "path", [str(launcher), str(tmp_path)])
        monkeypatch.setattr(
            pybuilder_module, "launcher_entry", lambda: str(launcher).upper()
        )

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [tmp_path.as_posix()]

    def test_no_recorded_launcher_leaves_every_entry(
        self,
        project: Project,
        env: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        marker = tmp_path / "marker"
        monkeypatch.setattr(sys, "path", [str(marker)])
        monkeypatch.setattr(pybuilder_module, "launcher_entry", lambda: None)

        @env.PyBuilder()
        def report(targets, sources):
            return 1

        made = report(target="report.txt", source=["a.txt"])
        project.resolve()

        assert payload_of(made, tmp_path)["path"] == [marker.as_posix()]


class TestEmitter:
    """A builder that works out its own targets when the build resolves."""

    def test_the_emitter_names_the_target(self, project: Project, env: Any) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, repo, **kw: (
                [f"{repo}.stamp"],
                sources,
            )
        )
        def fetch(targets, sources, repo, url):
            return None

        made = fetch(repo="gcc", url="https://example.invalid/gcc.git")
        project.resolve()

        assert made.output_nodes[0].path.as_posix() == "build/gcc.stamp"

    def test_the_emitter_runs_at_resolve(self, project: Project, env: Any) -> None:
        """Not at the call: what it reads may not have been built yet."""
        calls: list[int] = []

        def emitter(targets, sources, env, **kwargs):
            calls.append(1)
            return [*targets, "extra.txt"], sources

        @env.PyBuilder(emitter=emitter)
        def report(targets, sources):
            return None

        report(target="out.txt", source=["a.txt"])
        assert calls == []

        project.resolve()
        assert calls == [1]

    def test_the_emitter_adds_a_target(self, project: Project, env: Any) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, **kw: (
                [*targets, "out.log"],
                sources,
            )
        )
        def report(targets, sources):
            return None

        made = report(target="out.txt", source=["a.txt"])
        project.resolve()

        assert [n.path.as_posix() for n in made.output_nodes] == [
            "build/out.txt",
            "build/out.log",
        ]
        assert tokens(made)[4:6] == ["--n-targets", "2"]

    def test_the_emitter_adds_a_source(self, project: Project, env: Any) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, **kw: (targets, [*sources, "b.txt"])
        )
        def report(targets, sources):
            return None

        made = report(target="out.txt", source=["a.txt"])
        project.resolve()

        assert source_paths(made) == ["a.txt", "b.txt"]

    def test_the_emitter_sees_the_environment(self, project: Project, env: Any) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, environment, **kw: (
                [environment.build_dir / "named.txt"],
                sources,
            )
        )
        def report(targets, sources):
            return None

        made = report(source=["a.txt"])
        project.resolve()

        assert made.output_nodes[0].path.as_posix() == "build/named.txt"

    def test_the_same_keywords_reach_the_emitter_and_the_function(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, stem, **kw: (
                [f"{stem}.txt"],
                sources,
            )
        )
        def report(targets, sources, stem, title):
            return None

        made = report(stem="counts", title="Counts", source=["a.txt"])
        project.resolve()

        assert made.output_nodes[0].path.as_posix() == "build/counts.txt"
        assert payload_of(made, tmp_path)["kwargs"] == {
            "stem": "counts",
            "title": "Counts",
        }

    def test_a_target_named_after_a_program(self, project: Project, env: Any) -> None:
        """The real use: a name only the resolved program knows.

        The program's file name is the linker's business, so no call can
        write it out; the emitter reads it off the resolved target instead.
        """
        program = env.Command(
            target=lambda: "firmware.elf",
            source=["a.txt"],
            command=["link", "$SOURCE", "$TARGET"],
        )

        @env.PyBuilder(
            emitter=lambda targets, sources, env, **kw: (
                [f"{sources[0].output_nodes[0].path.stem}.hex"],
                sources,
            )
        )
        def to_hex(targets, sources):
            return None

        made = to_hex(source=[program])
        project.resolve()

        assert made.output_nodes[0].path.as_posix() == "build/firmware.hex"
        assert source_paths(made) == ["build/firmware.elf"]

    def test_a_target_source_the_emitter_drops_is_only_ordered(
        self, project: Project, env: Any
    ) -> None:
        """The emitter reads the call's Targets at resolve, so they resolve
        first; one it drops is no input, and never reruns the edge."""
        first = env.Command(
            target="made.txt", source=["a.txt"], command=["cp", "$SOURCE", "$TARGET"]
        )

        @env.PyBuilder(emitter=lambda targets, sources, env, **kw: (["out.txt"], []))
        def report(targets, sources):
            return None

        made = report(source=[first])
        assert first in made.dependencies
        project.resolve()

        assert source_paths(made) == []
        assert "build/made.txt" not in implicit_deps(made)

    def test_an_unnamed_edge_is_labelled_by_its_output(
        self, project: Project, env: Any
    ) -> None:
        """No target at the call, and no name needed: the edge wears its
        output's path once the emitter has named it, as any command does."""

        @env.PyBuilder(
            emitter=lambda targets, sources, env, n, **kw: ([f"out{n}.txt"], sources)
        )
        def render(targets, sources, n):
            return None

        first = render(n=1)
        named = render(n=2, name="second")
        project.resolve()

        assert (first.name, named.name) == ("out1.txt", "second")
        assert first.anonymous

    def test_the_pickle_is_named_after_the_emitted_target(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, n, **kw: ([f"out{n}.txt"], sources)
        )
        def render(targets, sources, n):
            return None

        made = render(n=1)
        render(n=2)
        project.resolve()

        assert node_tokens(made) == [
            RUNNER,
            "build/pybuilder/render.py",
            "build/pybuilder/out1.txt.args.pkl",
        ]
        assert sorted(q.name for q in (tmp_path / "build" / "pybuilder").iterdir()) == [
            "out1.txt.args.pkl",
            "out2.txt.args.pkl",
            "pcons-runner",
            "render.py",
        ]

    def test_the_generated_ninja_names_the_emitted_target(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        @env.PyBuilder(
            emitter=lambda targets, sources, env, **kw: (["emitted.txt"], sources)
        )
        def render(targets, sources):
            return None

        render(source=["a.txt"])
        text = ninja_text(project, tmp_path)

        assert "build emitted.txt: " in text
        assert "emitted.txt.args.pkl" in text


class TestEmitterRefusals:
    def test_no_target_and_no_emitter(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources):
            return None

        with pytest.raises(PyBuilderError, match="needs target="):
            report(source=["a.txt"])

    def test_an_emitter_that_is_not_callable(self, env: Any) -> None:
        """Refused at the decoration, which is where it was written."""
        with pytest.raises(PyBuilderError, match="is not callable"):
            env.PyBuilder(emitter="out.txt")  # ty: ignore[invalid-argument-type]

    def test_an_emitter_that_cannot_take_the_keywords(
        self, project: Project, env: Any
    ) -> None:
        """Refused at the call, which is where the keywords were written."""

        @env.PyBuilder(emitter=lambda targets, sources, env: (targets, sources))
        def report(targets, sources, title):
            return None

        with pytest.raises(PyBuilderError, match="cannot be called with title"):
            report(target="out.txt", title="Counts")

    def test_an_emitter_that_returns_one_list(self, project: Project, env: Any) -> None:
        @env.PyBuilder(emitter=lambda targets, sources, env, **kw: ["out.txt"])
        def report(targets, sources):
            return None

        report(source=["a.txt"])

        with pytest.raises(PyBuilderError, match="returns the pair"):
            project.resolve()

    def test_an_emitter_that_returns_no_target(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder(emitter=lambda targets, sources, env, **kw: ([], sources))
        def report(targets, sources):
            return None

        report(source=["a.txt"])

        with pytest.raises(PconsError, match="must return the file"):
            project.resolve()


@dataclass
class Settings:
    """A user's own object holding a target, which expands where it sits."""

    program: Any
    level: int


class TestArgumentsNameTheBuild:
    """A keyword may hold a target, a node or a Subst, read at resolve."""

    def test_a_target_arrives_as_its_files(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """Its outputs, as the function opens them from where it runs; it's
        named lazily here, so only resolve has the path."""
        program = env.Command(
            target=lambda: "firmware.elf",
            source=["a.txt"],
            command=["link", "$SOURCE", "$TARGET"],
        )

        @env.PyBuilder()
        def report(targets, sources, program):
            return None

        made = report(target="out.txt", program=program)
        project.resolve()

        assert payload_of(made, tmp_path)["kwargs"] == {"program": ["firmware.elf"]}

    def test_a_target_is_a_dependency_that_reruns_the_edge(
        self, project: Project, env: Any
    ) -> None:
        """The function reads it, so a change to it reruns the function."""
        program = env.Command(
            target="firmware.elf", source=["a.txt"], command=["link", "$TARGET"]
        )

        @env.PyBuilder()
        def report(targets, sources, program):
            return None

        made = report(target="out.txt", program=program)
        assert program in made.dependencies
        project.resolve()

        assert "build/firmware.elf" in implicit_deps(made)

    def test_a_source_node_is_seen_from_the_build_directory(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The function runs in the build directory, so a source's path climbs
        out of it, as its own sources' paths do."""

        @env.PyBuilder()
        def report(targets, sources, data):
            return None

        made = report(target="out.txt", data=project.node("a.txt"))
        project.resolve()

        assert payload_of(made, tmp_path)["kwargs"] == {"data": "../a.txt"}
        assert "a.txt" in implicit_deps(made)

    def test_a_marker_expands_where_it_sits(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """In a list, a dictionary value, or an object's attribute alike."""
        program = env.Command(
            target="firmware.elf", source=["a.txt"], command=["link", "$TARGET"]
        )

        @env.PyBuilder()
        def report(targets, sources, nested, settings):
            return None

        made = report(
            target="out.txt",
            nested={"programs": [program, 3]},
            settings=Settings(program, 2),
        )
        project.resolve()

        kwargs = payload_of(made, tmp_path)["kwargs"]
        assert kwargs["nested"] == {"programs": [["firmware.elf"], 3]}
        assert kwargs["settings"] == Settings(["firmware.elf"], 2)

    def test_a_subst_is_the_value_resolve_settled(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """Expanded in the edge's environment when it resolves, so a flag set
        after the call is there, as it is for a compile."""
        env.add_tool("tool")
        env.tool.flags = ["-O1"]

        @env.PyBuilder()
        def report(targets, sources, flags, literal):
            return None

        made = report(target="out.txt", flags=Subst("$tool.flags"), literal="$x")
        env.tool.flags = ["-O2", "-Wall"]
        project.resolve()

        assert payload_of(made, tmp_path)["kwargs"] == {
            "flags": ["-O2", "-Wall"],
            "literal": "$x",
        }

    def test_a_path_in_a_subst_is_seen_from_the_build_directory(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        env.add_tool("tool")
        env.tool.iprefix = "-I"
        env.tool.includes = [ProjectPath("inc")]

        @env.PyBuilder()
        def report(targets, sources, flags):
            return None

        made = report(
            target="out.txt", flags=Subst("${prefix(tool.iprefix, tool.includes)}")
        )
        project.resolve()

        assert payload_of(made, tmp_path)["kwargs"] == {"flags": ["-I../inc"]}

    def test_paths_follow_the_edges_cwd(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The function runs where cwd= says, so its paths start there."""
        (tmp_path / "work").mkdir()
        program = env.Command(
            target="firmware.elf", source=["a.txt"], command=["link", "$TARGET"]
        )

        @env.PyBuilder(cwd="work")
        def report(targets, sources, program, data):
            return None

        made = report(target="out.txt", program=program, data=project.node("a.txt"))
        project.resolve()

        assert payload_of(made, tmp_path)["kwargs"] == {
            "program": ["../build/firmware.elf"],
            "data": "../a.txt",
        }


class TestArgumentRefusalsAtTheCall:
    """What the call can settle, it settles, before a marker has a value."""

    def test_an_unpicklable_argument_beside_a_marker(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources, data, handle):
            return None

        with pytest.raises(PyBuilderError, match="cannot pickle argument handle"):
            report(target="out.txt", data=project.node("a.txt"), handle=lambda: 1)

    def test_an_alias_says_to_pass_its_targets(
        self, project: Project, env: Any
    ) -> None:
        @env.PyBuilder()
        def report(targets, sources, group):
            return None

        with pytest.raises(PyBuilderError, match="Pass the targets it groups"):
            report(target="out.txt", group=project.Alias("everything"))


class TestArgumentRefusalsAtResolve:
    """What only resolve can see, blamed on the call that passed it."""

    def test_a_subst_that_does_not_expand(self, project: Project, env: Any) -> None:
        @env.PyBuilder()
        def report(targets, sources, flags):
            return None

        call_line = inspect.currentframe().f_lineno + 1  # ty: ignore[possibly-missing-attribute]
        report(target="out.txt", flags=Subst("$nosuch.flags"))

        with pytest.raises(PyBuilderError) as caught:
            project.resolve()

        assert "Subst('$nosuch.flags') does not expand" in str(caught.value)
        assert caught.value.location.lineno == call_line

    def test_a_target_that_builds_no_file(self, project: Project, env: Any) -> None:
        headers = project.HeaderOnlyLibrary("headers")

        @env.PyBuilder()
        def report(targets, sources, lib):
            return None

        report(target="out.txt", lib=headers)

        with pytest.raises(PyBuilderError, match="builds no file"):
            project.resolve()
