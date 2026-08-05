# SPDX-License-Identifier: MIT
"""Property tests for path escaping in generated build.ninja files.

Escaping is where build-file generators bleed: a space, a dollar or a
colon in a filename has to survive the build statement, the command line
and the per-edge variables, and each of those is escaped by different
code. These build small projects out of deliberately awful filenames and
check that what comes out still means what went in -- first by reading
the file back with an independent lexer, then, where ninja is installed,
by running it.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys

import pytest
from hypothesis import HealthCheck, given, settings

from pcons import Generator, Project
from pcons.generators.generator import BaseGenerator

from . import ninja_lex
from .strategies import path_projects

pytestmark = pytest.mark.fuzz

fs_settings = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

# Running ninja costs a process per edge, so this property runs on a tenth
# of the examples the profile asks for: 5 in the fast suite, 200 nightly.
build_settings = settings(
    max_examples=max(1, settings.default.max_examples // 10),
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)

COPY = "import shutil,sys; shutil.copyfile(sys.argv[1], sys.argv[2])"


def build_project(tmp_path, spec):
    """Write the files `spec` describes and generate build.ninja for them.

    Returns (build_dir, expected), where `expected` maps each output path
    as ninja should see it to the input path it is copied from -- both
    relative to the build directory, which is where ninja runs.
    """
    subdir, files = spec
    # One directory per example: Hypothesis reuses the fixture, and these
    # examples write real files. Named by content, so a replay reuses it.
    digest = hashlib.sha256(repr(spec).encode()).hexdigest()[:16]
    root = tmp_path / digest
    if root.exists():
        shutil.rmtree(root)
    source_dir = root / subdir if subdir else root
    source_dir.mkdir(parents=True)

    Project._clear_tree()
    BaseGenerator._clear_pending()
    project = Project("fuzz", root_dir=root, build_dir="build")
    env = project.Environment()

    expected = {}
    for i, (source_name, output_name) in enumerate(files):
        (source_dir / source_name).write_text(f"contents {i}\n")
        relative = f"{subdir}/{source_name}" if subdir else source_name
        env.Command(
            name=f"copy{i}",
            target=output_name,
            source=relative,
            command=[sys.executable, "-c", COPY, "$SOURCE", "$TARGET"],
        )
        expected[output_name] = f"../{relative}"

    project.resolve()
    Generator().generate(project)
    BaseGenerator._generate_pending(project)
    return root / "build", expected


@fs_settings
@given(path_projects())
def test_generated_paths_survive_ninja_lexing(tmp_path, spec):
    """Every path reads back as itself under ninja's own escaping rules."""
    build_dir, expected = build_project(tmp_path, spec)

    # Read as ninja does: build files are UTF-8, whatever the locale is.
    text = (build_dir / "build.ninja").read_text(encoding="utf-8")
    builds = ninja_lex.parse(text)

    for output, source in expected.items():
        edges = [(outs, ins) for outs, ins in builds if output in outs]
        assert edges, f"no build statement produces {output!r}"
        assert any(source in ins for _, ins in edges), (
            f"{output!r} is not built from {source!r}"
        )


@pytest.mark.skipif(shutil.which("ninja") is None, reason="ninja not installed")
@build_settings
@given(path_projects())
def test_ninja_builds_and_second_run_is_a_no_op(tmp_path, spec):
    """ninja builds these paths, and then agrees there is nothing left to do.

    The second run is the sharper half: it fails when the path pcons
    wrote in the build statement is not quite the path the command
    produced, which a single successful build hides.
    """
    build_dir, expected = build_project(tmp_path, spec)

    first = subprocess.run(
        ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
    )
    assert first.returncode == 0, first.stderr or first.stdout
    for output in expected:
        assert (build_dir / output).exists(), f"{output!r} was not produced"

    second = subprocess.run(
        ["ninja"], cwd=build_dir, capture_output=True, text=True, check=False
    )
    assert second.returncode == 0, second.stderr or second.stdout
    assert "no work to do" in second.stdout, second.stdout


def test_hostile_alphabet_is_not_empty():
    """Guard against the strategy quietly degenerating to boring names."""
    from .strategies import NAME_CHARS

    assert " " in NAME_CHARS and "$" in NAME_CHARS
    if platform.system() != "Windows":
        assert ":" in NAME_CHARS
