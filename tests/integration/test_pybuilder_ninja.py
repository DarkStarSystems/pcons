# SPDX-License-Identifier: MIT
"""End-to-end: a PyBuilder edge built by real ninja.

The only test that proves the three halves agree: what the decorator emits,
what the generator writes, and what the runner does with the argv it gets.
Everything else asserts against strings.
"""

from __future__ import annotations

import importlib.util
import os
import pickle
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.workers.python import PythonWorker
from tests.support import EXE_SUFFIX, subprocess_env

needs_ninja = pytest.mark.skipif(
    shutil.which("ninja") is None, reason="ninja not installed"
)
posix_only = pytest.mark.skipif(os.name == "nt", reason="workers need AF_UNIX")


def generate(project: Project) -> None:
    """Write build.ninja for *project*."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)


def build(tmp_path: Path) -> str:
    """Run ninja in the build directory and return what it said."""
    result = subprocess.run(
        ["ninja"],
        cwd=tmp_path / "build",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def write_script(tmp_path: Path, name: str, text: str) -> ModuleType:
    """Write *text* to a real file and import it, so its ``__file__`` is real.

    A build script run by pcons gets its own directory inserted on
    ``sys.path`` before it runs, ``pcons/cli.py``. This writes the same
    thing here so the decoration inside *text* captures a directory that
    really holds the module it decorates.
    """
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT_WITH_HELPER = """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def make_report(env, **how):
    @env.PyBuilder(**how)
    def report(targets, sources):
        import pybuilder_helper
        from pathlib import Path

        Path(targets[0]).write_text(pybuilder_helper.value(), encoding="utf-8")

    return report
"""


def helper_script_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **how: Any
) -> Project:
    """A project whose PyBuilder edge imports a module beside its own script."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "pybuilder_helper.py").write_text(
        "def value():\n    return 'from beside the script'\n", encoding="utf-8"
    )
    script = write_script(tmp_path, "helper_script", SCRIPT_WITH_HELPER)
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()
    script.make_report(env, **how)(target="report.txt")
    generate(project)
    return project


@needs_ninja
def test_a_module_beside_the_script_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper_script_project(tmp_path, monkeypatch)

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "from beside the script"


@needs_ninja
@posix_only
def test_a_module_beside_the_script_imports_under_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper_script_project(tmp_path, monkeypatch, worker=PythonWorker(idle_timeout=5))

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "from beside the script"


def sources_project(tmp_path: Path, title: str) -> Project:
    """A project whose single PyBuilder concatenates two files under a title."""
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyBuilder()
    def report(targets, sources, title):
        from pathlib import Path

        body = "".join(Path(name).read_text(encoding="utf-8") for name in sources)
        Path(targets[0]).write_text(f"{title}\n{body}", encoding="utf-8")

    report(target="report.txt", source=["a.txt", "b.txt"], title=title)
    generate(project)
    return project


@needs_ninja
def test_the_function_runs_and_writes_its_target(tmp_path: Path) -> None:
    sources_project(tmp_path, "Report")

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Report\nfirst\nsecond\n"


@needs_ninja
def test_a_second_build_has_nothing_to_do(tmp_path: Path) -> None:
    """The generated module and pickle must not move on a rerun."""
    sources_project(tmp_path, "Report")
    build(tmp_path)

    assert "no work to do" in build(tmp_path)


@needs_ninja
def test_changed_kwargs_rebuild_the_target(tmp_path: Path) -> None:
    """The pickle is a real input of the edge, not a file beside it."""
    sources_project(tmp_path, "Report")
    build(tmp_path)

    Project._clear_tree()
    sources_project(tmp_path, "Second")
    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Second\nfirst\nsecond\n"


@needs_ninja
def test_a_changed_source_rebuilds_the_target(tmp_path: Path) -> None:
    sources_project(tmp_path, "Report")
    build(tmp_path)

    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")

    build(tmp_path)

    assert (tmp_path / "build" / "report.txt").read_text(
        encoding="utf-8"
    ) == "Report\nchanged\nsecond\n"


@needs_ninja
def test_two_targets_and_no_sources(tmp_path: Path) -> None:
    """``--n-targets 2`` and an empty source tail, split by the runner."""
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyBuilder()
    def split(targets, sources, n):
        from pathlib import Path

        for index, name in enumerate(targets):
            Path(name).write_text(f"{index} of {n}, {len(sources)} sources\n")

    split(target=["one.txt", "two.txt"], n=2)
    generate(project)
    build(tmp_path)

    assert (tmp_path / "build" / "one.txt").read_text(
        encoding="utf-8"
    ) == "0 of 2, 0 sources\n"
    assert (tmp_path / "build" / "two.txt").read_text(
        encoding="utf-8"
    ) == "1 of 2, 0 sources\n"


@needs_ninja
def test_one_function_makes_two_edges_from_one_module(tmp_path: Path) -> None:
    """The whole point of the builder shape, end to end.

    One decoration, two calls: one generated module, two pickles, two edges,
    and ninja builds both.
    """
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyBuilder()
    def report(targets, sources, title):
        from pathlib import Path

        body = "".join(Path(name).read_text(encoding="utf-8") for name in sources)
        Path(targets[0]).write_text(f"{title}\n{body}", encoding="utf-8")

    report(target="both.txt", source=["a.txt", "b.txt"], title="Both")
    report(target="just_a.txt", source=["a.txt"], title="Just a")
    generate(project)
    build(tmp_path)

    generated = sorted(q.name for q in (tmp_path / "build" / "pybuilder").iterdir())
    assert [q for q in generated if q.endswith((".py", ".pkl"))] == [
        "both.txt.args.pkl",
        "just_a.txt.args.pkl",
        "report.py",
    ]
    assert (tmp_path / "build" / "both.txt").read_text(
        encoding="utf-8"
    ) == "Both\nfirst\nsecond\n"
    assert (tmp_path / "build" / "just_a.txt").read_text(
        encoding="utf-8"
    ) == "Just a\nfirst\n"


@needs_ninja
def test_a_subdirectory_builds_its_own_edge(tmp_path: Path) -> None:
    """The path a plain build-relative source would get wrong."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("from the subdirectory\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    with project._enter_subdir("sub"):
        child = Project("child", root_dir=tmp_path / "sub")
        env: Any = child.Environment()

        @env.PyBuilder()
        def report(targets, sources):
            from pathlib import Path

            Path(targets[0]).write_text(
                Path(sources[0]).read_text(encoding="utf-8"), encoding="utf-8"
            )

        report(target="report.txt", source=["a.txt"])

    generate(project)
    build(tmp_path)

    assert (tmp_path / "build" / "sub" / "report.txt").read_text(
        encoding="utf-8"
    ) == "from the subdirectory\n"


def shadowing_project(tmp_path: Path, **how: Any) -> None:
    """Two functions named after standard modules, one importing the other's name.

    Each writes a generated module into ``build/pybuilder``, ``pickle.py`` and
    ``json.py``, which the runner itself and the body's own import would pick
    up if the runner ran from that directory.
    """
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()

    @env.PyBuilder(**how)
    def pickle(targets, sources):
        from pathlib import Path

        Path(targets[0]).write_text("ran", encoding="utf-8")

    @env.PyBuilder(**how)
    def json(targets, sources):
        import json as imported
        from pathlib import Path

        Path(targets[0]).write_text(imported.dumps([1]), encoding="utf-8")

    pickle(target="pickled.txt")
    json(target="dumped.txt")
    generate(project)


@needs_ninja
def test_a_function_named_like_a_standard_module_does_not_shadow_it(
    tmp_path: Path,
) -> None:
    shadowing_project(tmp_path)

    build(tmp_path)

    assert (tmp_path / "build" / "pickled.txt").read_text(encoding="utf-8") == "ran"
    assert (tmp_path / "build" / "dumped.txt").read_text(encoding="utf-8") == "[1]"


@needs_ninja
@posix_only
def test_a_worker_runs_the_runner_copy_without_shadowing(tmp_path: Path) -> None:
    """The worker chdirs to the build directory and puts the script's own
    directory on ``sys.path``, the two things the copy depends on."""
    shadowing_project(tmp_path, worker=PythonWorker(idle_timeout=5))

    build(tmp_path)

    assert (tmp_path / "build" / "pickled.txt").read_text(encoding="utf-8") == "ran"
    assert (tmp_path / "build" / "dumped.txt").read_text(encoding="utf-8") == "[1]"


@needs_ninja
def test_touching_the_runner_copy_reruns_every_pybuilder_edge_and_nothing_else(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("first\n", encoding="utf-8")
    project = Project("e2e", root_dir=tmp_path)
    env: Any = project.Environment()
    host = project.Environment(name="host")
    host.build_prefix = "host"

    def make(environment: Any) -> Any:
        @environment.PyBuilder()
        def report(targets, sources):
            from pathlib import Path

            Path(targets[0]).write_text("report", encoding="utf-8")

        return report

    make(env)(target="one.txt", source=["a.txt"])
    make(host)(target="two.txt", source=["a.txt"])
    env.Command(
        target="copy.txt",
        source=["a.txt"],
        command=[
            sys.executable,
            "-c",
            "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])",
            "$SOURCE",
            "$TARGET",
        ],
    )
    generate(project)
    build(tmp_path)

    later = time.time() + 10
    runner = tmp_path / "build" / "pybuilder" / "pcons-runner" / "pcons-runner.py"
    os.utime(runner, (later, later))
    rerun = build(tmp_path)

    assert "one.txt" in rerun
    assert "two.txt" in rerun
    assert "copy.txt" not in rerun
    assert not (tmp_path / "build" / "host" / "pybuilder" / "pcons-runner").exists()


LAUNCHER_PROBE_SCRIPT = """
from pcons import Project

project = Project("launcher_probe")
env = project.Environment()


@env.PyBuilder()
def probe(targets, sources):
    from pathlib import Path

    Path(targets[0]).write_text("ok", encoding="utf-8")


probe(target="probe.txt")
"""


def _console_script() -> Path | None:
    """Where the ``pcons`` console script would live beside this interpreter.

    None in an environment that never materialized entry-point scripts, in
    which case the tests that need it are skipped rather than guessing a
    location.
    """
    candidate = Path(sys.executable).parent / f"pcons{EXE_SUFFIX}"
    return candidate if candidate.is_file() else None


needs_console_script = pytest.mark.skipif(
    _console_script() is None,
    reason="no pcons console script beside this interpreter",
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env=subprocess_env(),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def _probe_sys_path(build_dir: Path) -> list[str] | None:
    payload = pickle.loads(
        (build_dir / "pybuilder" / "probe.txt.args.pkl").read_bytes()
    )
    return payload["path"]


class TestTheLauncherEntry:
    """The captured ``sys.path`` does not depend on how pcons was launched."""

    def _write(self, tmp_path: Path) -> Path:
        script = tmp_path / "pcons-build.py"
        script.write_text(LAUNCHER_PROBE_SCRIPT, encoding="utf-8")
        return script

    @needs_console_script
    def test_m_pcons_and_the_console_script_capture_the_same_path(
        self, tmp_path: Path
    ) -> None:
        self._write(tmp_path)

        _run([sys.executable, "-m", "pcons", "generate", "-B", "build_m"], tmp_path)
        _run([str(_console_script()), "generate", "-B", "build_console"], tmp_path)

        assert _probe_sys_path(tmp_path / "build_m") == _probe_sys_path(
            tmp_path / "build_console"
        )

    @needs_ninja
    @needs_console_script
    def test_a_console_script_generate_survives_ninjas_own_regenerate(
        self, tmp_path: Path
    ) -> None:
        script = self._write(tmp_path)

        _run([str(_console_script()), "generate"], tmp_path)
        build_dir = tmp_path / "build"
        build(tmp_path)
        first_path = _probe_sys_path(build_dir)

        script.touch()
        rebuild = build(tmp_path)

        assert "probe" not in rebuild
        assert _probe_sys_path(build_dir) == first_path
