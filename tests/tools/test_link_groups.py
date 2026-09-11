# SPDX-License-Identifier: MIT
"""Static libraries that link each other (#120).

Such a cycle is allowed: it has no build order to break, and every linker
can resolve it, GNU ld and lld given a group. The link puts the group on
the command line through the LINK_GROUPS variable, with the archives kept as
implicit dependencies so the link still waits for them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pcons import Project
from pcons.core.errors import DependencyCycleError
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator


def _project(tmp_path: Path, gcc_toolchain) -> tuple[Project, object]:
    for name in ("a", "b", "c", "d", "main"):
        (tmp_path / f"{name}.c").write_text(f"int {name}(void) {{ return 0; }}\n")
    project = Project("t", root_dir=tmp_path, build_dir="build")
    return project, project.Environment(toolchain=gcc_toolchain)


def _generate(project: Project, generator=None) -> str:
    project.resolve()
    (generator or NinjaGenerator()).generate(project)
    BaseGenerator._generate_pending(project)
    name = "Makefile" if isinstance(generator, MakefileGenerator) else "build.ninja"
    return (project.root_dir / "build" / name).read_text().replace("\\", "/")


def _link_edge(text: str, output: str) -> list[str]:
    """The build statement for *output* (its stem, so ``main.exe`` on
    Windows) and its indented variable lines."""
    lines = text.splitlines()
    start = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith("build ")
        and output in (Path(o).stem for o in ln.split(":", 1)[0].split())
    )
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    return lines[start:end]


def _archive(target) -> str:
    """The archive's file name (``liba.a`` here, ``a.lib`` on Windows)."""
    return target.output_nodes[0].path.name


@pytest.fixture
def linux_platform():
    with patch("pcons.toolchains.unix.get_platform") as mock_platform:
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        yield


@pytest.fixture
def macos_platform():
    with patch("pcons.toolchains.unix.get_platform") as mock_platform:
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        yield


class TestTheCycleIsAllowed:
    def test_two_static_libraries_resolve(self, tmp_path, gcc_toolchain):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        a.link(b)
        b.link(a)
        project.Program("main", env, sources=["main.c"]).link(a)

        project.resolve()

        assert a.output_nodes and b.output_nodes
        assert project.validate() == []

    def test_object_and_header_only_libraries_may_join(self, tmp_path, gcc_toolchain):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        objs = project.ObjectLibrary("objs", env, sources=["b.c"])
        hdrs = project.HeaderOnlyLibrary("hdrs")
        a.link(objs)
        objs.link(hdrs)
        hdrs.link(a)

        project.resolve()

        assert a.output_nodes and objs.output_nodes

    def test_a_program_in_the_cycle_is_refused(self, tmp_path, gcc_toolchain):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        main = project.Program("main", env, sources=["main.c"])
        a.link(main)
        main.link(a)

        with pytest.raises(DependencyCycleError, match="t::main is a program") as info:
            project.resolve()
        assert "t::a -> t::main -> t::a" in str(info.value)

    def test_a_shared_library_in_the_cycle_is_refused(self, tmp_path, gcc_toolchain):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.SharedLibrary("b", env, sources=["b.c"])
        a.link(b)
        b.link(a)

        with pytest.raises(DependencyCycleError, match="t::b is a shared_library"):
            project.resolve()


class TestTheLinkLine:
    def test_gnu_ld_gets_a_group(self, tmp_path, gcc_toolchain, linux_platform):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        a.link(b)
        b.link(a)
        project.Program("main", env, sources=["main.c"]).link(a)

        edge = _link_edge(_generate(project), "main")

        lib_a, lib_b = _archive(a), _archive(b)
        assert (
            f"  LINK_GROUPS = -Wl,--start-group {lib_a} {lib_b} -Wl,--end-group" in edge
        )
        explicit, implicit = edge[0].split(":", 1)[1].split(" | ", 1)
        assert lib_a not in explicit and lib_b not in explicit
        assert lib_a in implicit and lib_b in implicit

    def test_the_group_spans_the_libraries_between_the_members(
        self, tmp_path, gcc_toolchain, linux_platform
    ):
        """The link line keeps its order; the group closes around whatever
        lies between the first and last member, and the archives the cycle
        needs still follow it."""
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        c = project.StaticLibrary("c", env, sources=["c.c"])
        d = project.StaticLibrary("d", env, sources=["d.c"])
        a.link(b)
        b.link(c)
        b.link(a)
        c.link(d)
        project.Program("main", env, sources=["main.c"]).link(a)

        edge = _link_edge(_generate(project), "main")

        (groups,) = [ln for ln in edge if ln.startswith("  LINK_GROUPS = ")]
        libs = groups.split(" = ", 1)[1].split()
        at = {t: libs.index(_archive(t)) for t in (a, b, c, d)}
        assert libs[0] == "-Wl,--start-group"
        assert libs.index("-Wl,--end-group") > at[b]
        assert at[d] > at[c] > at[b] > at[a]

    def test_apple_ld_needs_nothing(self, tmp_path, gcc_toolchain, macos_platform):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        a.link(b)
        b.link(a)
        project.Program("main", env, sources=["main.c"]).link(a)

        edge = _link_edge(_generate(project), "main")

        assert not any("LINK_GROUPS" in ln for ln in edge)
        assert f"{_archive(a)} {_archive(b)}" in edge[0].split(" | ", 1)[0]

    def test_no_cycle_no_group(self, tmp_path, gcc_toolchain, linux_platform):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        a.link(b)
        project.Program("main", env, sources=["main.c"]).link(a)

        text = _generate(project)

        assert "LINK_GROUPS =" not in text
        libs = f"{_archive(a)} {_archive(b)}"
        assert libs in _link_edge(text, "main")[0].split(" | ", 1)[0]

    def test_make_expands_the_group_in_place(
        self, tmp_path, gcc_toolchain, linux_platform
    ):
        project, env = _project(tmp_path, gcc_toolchain)
        a = project.StaticLibrary("a", env, sources=["a.c"])
        b = project.StaticLibrary("b", env, sources=["b.c"])
        a.link(b)
        b.link(a)
        project.Program("main", env, sources=["main.c"]).link(a)

        text = _generate(project, MakefileGenerator())

        group = f"-Wl,--start-group {_archive(a)} {_archive(b)} -Wl,--end-group"
        assert group in text
