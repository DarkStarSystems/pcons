# SPDX-License-Identifier: MIT
"""End-to-end: a static library is written afresh on every rebuild (#229).

``ar rcs`` updates an existing archive in place: it keeps members whose
sources are gone, and fails outright on a macOS universal (fat) archive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import subprocess_env

pytestmark = [
    pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not installed"),
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("ar") is None, reason="needs ar to list members"
    ),
]

SCRIPT = """\
from pathlib import Path
from pcons import Project

project = Project("archive")
env = project.Environment(toolchain="c")
env.cc.flags.extend({flags!r})
sources = Path("sources.txt").read_text().split()
project.StaticLibrary("parts", env, sources=sources)
"""


def _project(tmp_path: Path, flags: list[str], sources: list[str]) -> None:
    for name in ("a", "b"):
        (tmp_path / f"{name}.c").write_text(f"int {name}(void) {{ return 1; }}\n")
    (tmp_path / "pcons-build.py").write_text(SCRIPT.format(flags=flags))
    (tmp_path / "sources.txt").write_text(" ".join(sources))


def _pcons(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pcons"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=subprocess_env(),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_removed_source_leaves_the_archive(tmp_path: Path) -> None:
    _project(tmp_path, [], ["a.c", "b.c"])
    _pcons(tmp_path)
    (tmp_path / "sources.txt").write_text("a.c")
    _pcons(tmp_path)

    members = subprocess.run(
        ["ar", "t", str(tmp_path / "build" / "libparts.a")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "a.c" in members
    assert "b.c" not in members


@pytest.mark.skipif(sys.platform != "darwin", reason="universal binaries are macOS")
def test_a_universal_archive_rebuilds(tmp_path: Path) -> None:
    _project(tmp_path, ["-arch", "arm64", "-arch", "x86_64"], ["a.c", "b.c"])
    _pcons(tmp_path)
    (tmp_path / "a.c").write_text("int a(void) { return 2; }\n")
    _pcons(tmp_path)
