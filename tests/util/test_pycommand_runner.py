# SPDX-License-Identifier: MIT
"""Tests for the build-time runner in pcons.util.pycommand."""

from __future__ import annotations

import pickle
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from pcons.util import pycommand
from pcons.util.pycommand import PROTOCOL_VERSION, USAGE, main, run

RUNNER = pycommand.__file__

WRITE_SOURCES = """
def render(sources, targets):
    with open(targets[0], "w") as out:
        out.write("|".join(sources))
"""

WITH_KWARGS = """
def render(sources, targets, n, label):
    with open(targets[0], "w") as out:
        out.write(f"{label}:{n}:{len(sources)}")
"""

NO_PATHS = """
def render(sources, targets, out_path):
    with open(out_path, "w") as out:
        out.write(f"{sources}{targets}")
"""

RAISES = """
class Boom(Exception):
    pass


def render(sources, targets):
    raise Boom("the function failed")
"""

RAISES_ON_IMPORT = """
raise RuntimeError("the module failed to import")


def render(sources, targets):
    return 1
"""

DATACLASS = """
import pickle
from dataclasses import dataclass


@dataclass
class Point:
    x: int


BLOB = pickle.dumps(Point(3))


def render(sources, targets):
    with open(targets[0], "wb") as out:
        out.write(BLOB)
"""


def write_module(tmp_path: Path, name: str, body: str) -> Path:
    """Write a generated-looking module and return its path."""
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def write_args(
    tmp_path: Path,
    name: str,
    function: str = "render",
    kwargs: dict[str, Any] | None = None,
    version: int = PROTOCOL_VERSION,
) -> Path:
    """Write a sidecar payload pickle and return its path."""
    path = tmp_path / f"{name}.args.pkl"
    payload = {
        "version": version,
        "module": name,
        "function": function,
        "kwargs": kwargs or {},
    }
    path.write_bytes(pickle.dumps(payload))
    return path


def build(
    tmp_path: Path,
    name: str,
    body: str,
    function: str = "render",
    kwargs: dict[str, Any] | None = None,
    version: int = PROTOCOL_VERSION,
) -> tuple[str, str]:
    """Write module and payload, returning the two paths the runner takes."""
    module = write_module(tmp_path, name, body)
    args = write_args(tmp_path, name, function, kwargs, version)
    return str(module), str(args)


class TestRun:
    """The runner calls the recorded function."""

    def test_sources_and_targets_reach_the_function(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "plain", WRITE_SOURCES)
        target = tmp_path / "out.txt"

        run(module, args, [str(target)], ["a.txt", "b.txt"])

        assert target.read_text() == "a.txt|b.txt"

    def test_kwargs_reach_the_function(self, tmp_path: Path) -> None:
        module, args = build(
            tmp_path, "kw", WITH_KWARGS, kwargs={"n": 3, "label": "run"}
        )
        target = tmp_path / "out.txt"

        run(module, args, [str(target)], ["a.txt"])

        assert target.read_text() == "run:3:1"

    def test_zero_targets_and_zero_sources(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.txt"
        module, args = build(tmp_path, "none", NO_PATHS, kwargs={"out_path": str(out)})

        run(module, args, [], [])

        assert out.read_text() == "[][]"

    def test_the_module_can_pickle_its_own_classes(self, tmp_path: Path) -> None:
        """Pickling happens while the module runs, which is the trap.

        The module must already be in ``sys.modules`` when its body executes:
        pickle stores a class by module name and looks that name up. Register
        it after ``exec_module`` and this raises ``PicklingError``.
        """
        module, args = build(tmp_path, "withdata", DATACLASS)
        target = tmp_path / "point.pkl"

        run(module, args, [str(target)], [])

        assert b"withdata" in target.read_bytes()

    def test_a_missing_function_names_itself(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "absent", WRITE_SOURCES, function="missing")

        with pytest.raises(AttributeError, match="missing"):
            run(module, args, [], [])

    def test_an_exception_propagates(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "boom", RAISES)

        with pytest.raises(Exception, match="the function failed"):
            run(module, args, [], [])

    def test_a_module_that_fails_to_import_leaves_nothing_behind(
        self, tmp_path: Path
    ) -> None:
        module, args = build(tmp_path, "brokenimport", RAISES_ON_IMPORT)

        with pytest.raises(RuntimeError, match="failed to import"):
            run(module, args, [], [])

        assert "brokenimport" not in sys.modules

    def test_a_stale_payload_version_names_the_fix(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "stale", WRITE_SOURCES, version=0)

        with pytest.raises(ValueError, match="Re-run pcons"):
            run(module, args, [], [])

    def test_a_payload_that_is_not_a_payload_names_the_fix(
        self, tmp_path: Path
    ) -> None:
        module = str(write_module(tmp_path, "notadict", WRITE_SOURCES))
        args = tmp_path / "notadict.args.pkl"
        args.write_bytes(pickle.dumps(["not", "a", "payload"]))

        with pytest.raises(ValueError, match="not a pycommand payload"):
            run(module, str(args), [], [])

    def test_a_payload_missing_a_key_names_the_fix(self, tmp_path: Path) -> None:
        module = str(write_module(tmp_path, "partial", WRITE_SOURCES))
        args = tmp_path / "partial.args.pkl"
        args.write_bytes(
            pickle.dumps({"version": PROTOCOL_VERSION, "module": "partial"})
        )

        with pytest.raises(ValueError, match="missing function, kwargs"):
            run(module, str(args), [], [])

    def test_an_unloadable_module_path_is_an_import_error(self, tmp_path: Path) -> None:
        args = write_args(tmp_path, "text")
        not_python = tmp_path / "text.txt"
        not_python.write_text("nope", encoding="utf-8")

        with pytest.raises(ImportError, match="text.txt"):
            run(str(not_python), str(args), [], [])


class TestMain:
    """Argument handling."""

    def test_targets_come_before_sources(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "order", WRITE_SOURCES)
        target = tmp_path / "out.txt"

        code = main([module, args, "--n-targets", "1", str(target), "a.txt", "b.txt"])

        assert code == 0
        assert target.read_text() == "a.txt|b.txt"

    def test_relative_paths_resolve_against_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What ninja does: run from the build directory, name paths from it."""
        gen = tmp_path / "pycmd"
        gen.mkdir()
        write_module(gen, "rel", WRITE_SOURCES)
        write_args(gen, "rel")
        monkeypatch.chdir(tmp_path)

        code = main(
            [
                "pycmd/rel.py",
                "pycmd/rel.args.pkl",
                "--n-targets",
                "1",
                "out.txt",
                "a.txt",
            ]
        )

        assert code == 0
        assert (tmp_path / "out.txt").read_text() == "a.txt"

    def test_no_arguments_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([]) == 1
        assert USAGE in capsys.readouterr().err

    def test_a_missing_n_targets_flag_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["mod.py", "args.pkl", "--targets", "1"]) == 1
        assert USAGE in capsys.readouterr().err

    def test_a_non_numeric_count_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["mod.py", "args.pkl", "--n-targets", "two"]) == 1
        assert USAGE in capsys.readouterr().err

    def test_a_negative_count_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["mod.py", "args.pkl", "--n-targets", "-1"]) == 1
        assert USAGE in capsys.readouterr().err

    def test_a_count_larger_than_the_paths_is_refused(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["mod.py", "args.pkl", "--n-targets", "2", "one.txt"]) == 1
        assert "only 1 paths" in capsys.readouterr().err

    def test_argv_defaults_to_sys_argv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module, args = build(tmp_path, "fromargv", WRITE_SOURCES)
        target = tmp_path / "out.txt"
        monkeypatch.setattr(
            sys,
            "argv",
            ["pycommand.py", module, args, "--n-targets", "1", str(target), "a.txt"],
        )

        assert main() == 0
        assert target.read_text() == "a.txt"


class TestScriptEntryPoint:
    """The runner is spawned as a script path, never as ``-m``.

    ``-m pcons.util.pycommand`` would execute ``pcons/__init__.py`` first,
    which costs the whole import of the generators and toolchains on every
    edge, and the persistent Python worker refuses an argv starting with a
    flag. The working directory below is deliberately not the repository:
    the script must run without pcons being importable.
    """

    def spawn(self, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, RUNNER, *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

    def test_it_runs_the_function(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "spawned", WRITE_SOURCES)
        target = tmp_path / "out.txt"

        result = self.spawn(
            tmp_path, module, args, "--n-targets", "1", str(target), "a.txt"
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text() == "a.txt"

    def test_a_path_with_a_space_stays_one_argument(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "spaced", WRITE_SOURCES)
        target = tmp_path / "out dir" / "out.txt"
        target.parent.mkdir()

        result = self.spawn(
            tmp_path, module, args, "--n-targets", "1", str(target), "a b.txt"
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text() == "a b.txt"

    def test_a_failing_function_exits_non_zero(self, tmp_path: Path) -> None:
        module, args = build(tmp_path, "spawnboom", RAISES)

        result = self.spawn(tmp_path, module, args, "--n-targets", "0")

        assert result.returncode != 0
        assert "the function failed" in result.stderr
        assert "spawnboom.py" in result.stderr

    def test_no_arguments_exits_non_zero(self, tmp_path: Path) -> None:
        result = self.spawn(tmp_path)

        assert result.returncode == 1
        assert USAGE in result.stderr
