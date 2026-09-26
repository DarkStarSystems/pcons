# SPDX-License-Identifier: MIT
"""Paths a script writes mean the same file standalone and under add_subdirectory.

A script reached through ``add_subdirectory`` reads every relative path from
its own directory, and ``project.build_dir`` from the top of the tree. Each
test declares something in a child script and checks where it landed; both
kinds of child script are covered, one with a Project of its own and one
reusing its parent's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.util.add_subdirectory import add_subdirectory

OWN_PROJECT = "from pcons.core.project import Project\nproject = Project('child')\n"
PARENT_PROJECT = "from pcons import context\nproject = context.current_project\n"


@pytest.fixture(params=[OWN_PROJECT, PARENT_PROJECT], ids=["own", "parent"])
def run_child(request, tmp_path, monkeypatch):
    """Run *body* as child/pcons-build.py under a top project; return its names."""
    monkeypatch.chdir(tmp_path)
    top = Project("top", root_dir=tmp_path, build_dir="build")
    top.Environment()
    child = tmp_path / "child"
    child.mkdir()

    def run(body: str):
        (child / "pcons-build.py").write_text(
            request.param + "env = project.default_environment\n" + body
        )
        return top, add_subdirectory("child")

    return run


def test_build_dir_sources_are_not_offset_again(run_child):
    _, ns = run_child(
        "gen = env.Command(target='gen/x.c', command='gen $TARGET')\n"
        "used = env.Command(target='y', source=project.build_dir / 'gen/x.c',"
        " command='use $SOURCE $TARGET')\n"
    )
    (source,) = ns.used.output_nodes[0]._build_info["sources"]
    assert source.path == Path("build/child/gen/x.c")


def test_node_reads_like_sources(run_child):
    _, ns = run_child("node = project.node('src/a.c')\n")
    assert ns.node.path == Path("child/src/a.c")


def test_build_dir_is_the_script_build_dir(run_child, tmp_path):
    _, ns = run_child("where = project.build_dir\n")
    assert ns.where == tmp_path / "build" / "child"


def test_srcdir_is_the_script_directory(run_child):
    _, ns = run_child(
        "cmd = env.Command(target='o', command='$SRCDIR/tool.py $TARGET')\n"
    )
    command = ns.cmd.output_nodes[0]._build_info["command"]
    assert "$SRCDIR/child/tool.py" in command


def test_env_build_dir_is_where_a_relative_target_lands(run_child, tmp_path):
    _, ns = run_child("where = env.build_dir\n")
    assert ns.where == tmp_path / "build" / "child"


def test_relative_cwd_is_the_script_directory(run_child, tmp_path):
    _, ns = run_child("cmd = env.Command(target='o', command='x', cwd='tools')\n")
    assert ns.cmd.output_nodes[0]._build_info["cwd"] == tmp_path / "child" / "tools"


def test_write_file_reads_like_sources(run_child, tmp_path):
    run_child(
        "from pcons import write_file\n"
        "write_file('notes.txt', 'src')\n"
        "write_file(project.build_dir / 'gen.h', 'gen')\n"
    )
    assert (tmp_path / "child" / "notes.txt").read_text() == "src"
    assert (tmp_path / "build" / "child" / "gen.h").read_text() == "gen"


def test_a_child_generator_writes_to_the_child_build_dir(run_child, tmp_path):
    top, _ = run_child(
        "from pcons.generators.mermaid import MermaidGenerator\n"
        "MermaidGenerator().generate(project)\n"
    )
    BaseGenerator._generate_pending(top)
    assert list((tmp_path / "build").glob("**/*.mmd"))
    assert not (tmp_path / "child" / "build").exists()
