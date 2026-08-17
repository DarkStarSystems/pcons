# SPDX-License-Identifier: MIT
"""``project.write_build_files()``: pcons embedded in a larger program (#90).

The embedded style describes a build inside its own program and asks for
the build files directly — no CLI, no deferred generation, no regen rule
unless it says how it is re-run.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from pcons.core.project import Project
from tests.support import subprocess_env

DRIVER = """\
from pathlib import Path

from pcons import Project

project = Project("embedded", root_dir=Path(__file__).parent)
project.write_build_files({regen})
"""


class TestEmbeddedDriver:
    """The real thing: `python driver.py`, in a subprocess."""

    def _run_driver(
        self, tmp_path: Path, regen: str = ""
    ) -> subprocess.CompletedProcess[str]:
        driver = tmp_path / "driver.py"
        driver.write_text(DRIVER.format(regen=regen))
        return subprocess.run(
            [sys.executable, str(driver)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
            env=subprocess_env(),
        )

    def test_writes_build_files_with_no_cli(self, tmp_path: Path) -> None:
        result = self._run_driver(tmp_path)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_the_direct_run_notice_stays_silent(self, tmp_path: Path) -> None:
        """The driver *is* argv[0], which used to earn the notice. A run
        that generated its build files was not run in vain."""
        result = self._run_driver(tmp_path)

        assert "run directly" not in result.stderr

    def test_no_regen_rule_without_a_command(self, tmp_path: Path) -> None:
        """argv names the embedder's program; re-running it as a build step
        is never right, so no rule is inferred."""
        self._run_driver(tmp_path)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "pcons_regen" not in content

    def test_a_regen_command_is_used_verbatim(self, tmp_path: Path) -> None:
        result = self._run_driver(
            tmp_path, regen='regen_command=["my-driver", "--rebuild"]'
        )

        assert result.returncode == 0, result.stderr
        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "pcons_regen" in content
        assert "my-driver --rebuild" in content


class TestWriteBuildFiles:
    """The in-process contract."""

    def test_each_sibling_writes_its_own_files(self, tmp_path: Path) -> None:
        first = Project("first", root_dir=tmp_path)
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        first.write_build_files()
        second.write_build_files()

        assert (tmp_path / "build" / "build.ninja").exists()
        assert (tmp_path / "build-b" / "build.ninja").exists()

    def test_the_same_regen_command_may_repeat(self, tmp_path: Path) -> None:
        """A driver with two projects names its command once per call."""
        first = Project("first", root_dir=tmp_path)
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        first.write_build_files(regen_command=["driver"])
        second.write_build_files(regen_command=["driver"])

        for build_dir in ("build", "build-b"):
            content = (tmp_path / build_dir / "build.ninja").read_text()
            assert "pcons_regen" in content

    def test_a_conflicting_regen_command_raises(self, tmp_path: Path) -> None:
        first = Project("first", root_dir=tmp_path)
        second = Project("second", root_dir=tmp_path, build_dir="build-b")

        first.write_build_files(regen_command=["driver"])
        with pytest.raises(ValueError, match="already owns the regen rule"):
            second.write_build_files(regen_command=["other-driver"])

    def test_under_a_recorded_run_a_regen_command_raises(self, tmp_path: Path) -> None:
        """Under pcons, the regen rule is the recorded invocation."""
        from pcons.core import invocation

        invocation.record(invocation.Invocation(script=tmp_path / "x.py"))
        project = Project("p", root_dir=tmp_path)

        with pytest.raises(ValueError, match="already owns the regen rule"):
            project.write_build_files(regen_command=["driver"])
