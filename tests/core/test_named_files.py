# SPDX-License-Identifier: MIT
"""A file a command names in its flags is a dependency of that command (#150).

A ``PathToken`` in a flag is read by the tool and reported by nothing, so
the edge would never rerun when the file changed. When the project knows
the file, because the build produces it or ``write_file`` wrote it, the
resolver makes it an implicit dependency. Directories are never nodes, so
include and library directories are left alone.
"""

from __future__ import annotations

from pathlib import Path

from pcons import Project, write_file
from pcons.core.subst import PathToken
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def _ninja(project: Project, root: Path) -> str:
    project.resolve()
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (root / "build" / "build.ninja").read_text().replace("\\", "/")


def _edge(text: str, output: str) -> str:
    """The build statement producing *output* (among its outputs: a DLL's
    edge lists its import library too)."""
    return next(
        ln
        for ln in text.splitlines()
        if ln.startswith("build ") and output in ln.split(":", 1)[0].split()
    )


class TestWriteFileRegistersItsOutput:
    def test_the_project_knows_the_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        path = write_file(project.build_dir / "exports.txt", "_main\n")

        assert project._canonicalize_path(path) in project._nodes

    def test_outside_a_project_is_not_an_error(self, tmp_path):
        assert write_file(tmp_path / "x.txt", "x").read_text() == "x"


class TestAFileNamedInFlagsIsADependency:
    def test_a_configure_time_file_in_link_flags(
        self, tmp_path, monkeypatch, gcc_toolchain
    ):
        """The export-list case: written at configure time, named in a
        link flag, and the link must rerun when its content changes."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.c").write_text("int a(void) { return 1; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        exports = write_file(project.build_dir / "exports.txt", "_a\n")
        lib = project.SharedLibrary("plug", env, sources=["a.c"])
        lib.private.link_flags.append(
            PathToken(prefix="-Wl,-exported_symbols_list,", path=str(exports))
        )

        text = _ninja(project, tmp_path)
        link = _edge(text, lib.output_nodes[0].path.relative_to("build").as_posix())

        assert "| exports.txt" in link

    def test_a_built_file_in_flags(self, tmp_path, monkeypatch, gcc_toolchain):
        """A Command's output named in a compile flag orders the compile
        after it and reruns the compile when it changes."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.c").write_text("int a(void) { return 1; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        gen = env.Command(
            target="flags.rsp", command="echo -DGEN=1 > $TARGET", name="gen"
        )
        lib = project.StaticLibrary("a", env, sources=["a.c"])
        lib.private.compile_flags.append(
            PathToken(prefix="@", path=str(gen.output_nodes[0].path))
        )

        text = _ninja(project, tmp_path)
        compile_ = _edge(text, "obj.a/a.c.o")

        assert "flags.rsp" in compile_.split("|", 1)[1]

    def test_an_include_directory_is_not(self, tmp_path, monkeypatch, gcc_toolchain):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "inc").mkdir()
        (tmp_path / "a.c").write_text("int a(void) { return 1; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        lib = project.StaticLibrary("a", env, sources=["a.c"])
        lib.private.include_dirs.append("inc")

        text = _ninja(project, tmp_path)
        compile_ = _edge(text, "obj.a/a.c.o")

        assert "| " not in compile_ or "inc" not in compile_.split("|", 1)[1]

    def test_a_build_relative_token(self, tmp_path, monkeypatch, gcc_toolchain):
        """A token written build-relative (path_type="build") names the
        same file as the node the build knows under the build directory."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.c").write_text("int a(void) { return 1; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(target="flags.rsp", command="echo -DGEN=1 > $TARGET", name="gen")
        lib = project.StaticLibrary("a", env, sources=["a.c"])
        lib.private.compile_flags.append(
            PathToken(prefix="@", path="flags.rsp", path_type="build")
        )

        text = _ninja(project, tmp_path)
        compile_ = _edge(text, "obj.a/a.c.o")

        assert "flags.rsp" in compile_.split("|", 1)[1]
