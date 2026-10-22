# SPDX-License-Identifier: MIT
"""max_jobs: a target's build steps run in a ninja pool of that depth."""

from __future__ import annotations

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def _ninja(tmp_path, gcc_toolchain, max_jobs) -> str:
    for name in ("a", "b", "main"):
        (tmp_path / f"{name}.c").write_text(f"int {name}(void) {{ return 0; }}\n")
    project = Project("t", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    heavy = project.StaticLibrary("heavy", env, sources=["a.c", "b.c"])
    heavy.set_option("max_jobs", max_jobs)
    project.Program("main", env, sources=["main.c"]).link(heavy)
    project.resolve()
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (tmp_path / "build" / "build.ninja").read_text()


def _statement(text: str, output: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"build {output}"))
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    return lines[start:end]


def test_the_targets_steps_share_a_pool(tmp_path, gcc_toolchain):
    text = _ninja(tmp_path, gcc_toolchain, 2)

    assert "pool max_jobs_t__heavy\n  depth = 2\n" in text
    assert text.index("pool max_jobs_t__heavy") < text.index("build ")
    compile_a = _statement(text, "obj.heavy.static/a.c")
    assert "  pool = max_jobs_t__heavy" in compile_a
    # The program is another target, outside the pool.
    assert not any(ln.startswith("  pool") for ln in _statement(text, "main"))


@pytest.mark.parametrize("bad", [0, -1, 1.5, "2", True])
def test_it_takes_a_positive_whole_number(tmp_path, gcc_toolchain, bad):
    with pytest.raises(ValueError, match="max_jobs"):
        _ninja(tmp_path, gcc_toolchain, bad)
