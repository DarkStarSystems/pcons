# SPDX-License-Identifier: MIT
"""A directory in output_name: the prefix and suffix go on the file's own name.

``output_name = "lib/foo"`` on a shared library writes ``lib/libfoo.so``,
not ``liblib/foo.so``, on every platform and for every kind of output that
takes its name from it. The tests pin the platform with ``building_for``, so
each one means the same on every host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Project
from pcons.configure.platform import get_platform
from pcons.core.paths import affix_filename, last_component
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from tests.support import building_for

LINUX = "x86_64-linux-gnu"
MACOS = "arm64-apple-darwin"
MINGW = "x86_64-w64-mingw32"
MSVC = "x86_64-pc-windows-msvc"


def _project(tmp_path: Path) -> Project:
    (tmp_path / "lib.c").write_text("int f(void) { return 1; }\n")
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")
    return Project("p", root_dir=tmp_path, build_dir="build")


def _path(target) -> str:
    return target.output_nodes[0].path.as_posix()


def _import_lib(target) -> str | None:
    outputs = target.output_nodes[0]._build_info.get("outputs", {})
    return outputs["import_lib"]["path"].as_posix() if "import_lib" in outputs else None


@pytest.fixture
def toolchain(request, gcc_toolchain, clang_cl_toolchain):
    """clang-cl for an MSVC target, gcc for everything else."""
    return clang_cl_toolchain if request.param == MSVC else gcc_toolchain


def _by_triple(cases):
    """Parametrize (triple, expected...) rows, feeding the triple to `toolchain`."""
    return pytest.mark.parametrize(
        ("toolchain", "triple", "expected"),
        [(triple, triple, expected) for triple, expected in cases],
        indirect=["toolchain"],
    )


class TestOutputNameWithADirectory:
    @_by_triple(
        [
            (LINUX, ("build/lib/libfoo.so", None)),
            (MACOS, ("build/lib/libfoo.dylib", None)),
            (MINGW, ("build/lib/foo.dll", None)),
            (MSVC, ("build/lib/foo.dll", "build/lib/foo.lib")),
        ]
    )
    def test_shared_library(self, tmp_path, toolchain, triple, expected):
        project = _project(tmp_path)
        with building_for(triple):
            env = project.Environment(toolchain=toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "lib/foo"
            project.resolve()

            assert (_path(lib), _import_lib(lib)) == expected

    @_by_triple(
        [
            (LINUX, "build/lib/libfoo.a"),
            (MACOS, "build/lib/libfoo.a"),
            (MINGW, "build/lib/libfoo.a"),
            (MSVC, "build/lib/foo.lib"),
        ]
    )
    def test_static_library(self, tmp_path, toolchain, triple, expected):
        project = _project(tmp_path)
        with building_for(triple):
            env = project.Environment(toolchain=toolchain)
            lib = project.StaticLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "lib/foo"
            project.resolve()

            assert _path(lib) == expected

    @_by_triple(
        [
            (LINUX, "build/bin/app"),
            (MACOS, "build/bin/app"),
            (MINGW, "build/bin/app.exe"),
            (MSVC, "build/bin/app.exe"),
        ]
    )
    def test_program(self, tmp_path, toolchain, triple, expected):
        project = _project(tmp_path)
        with building_for(triple):
            env = project.Environment(toolchain=toolchain)
            app = project.Program("app", env, sources=["main.c"])
            app.output_name = "bin/app"
            project.resolve()

            assert _path(app) == expected

    def test_a_backslash_separates_too(self, tmp_path, gcc_toolchain):
        project = _project(tmp_path)
        with building_for(LINUX):
            env = project.Environment(toolchain=gcc_toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "lib\\foo"
            project.resolve()

            assert _path(lib) == "build/lib/libfoo.so"

    def test_it_nests_below_the_library_directory(self, tmp_path, gcc_toolchain):
        project = _project(tmp_path)
        with building_for(LINUX):
            env = project.Environment(toolchain=gcc_toolchain)
            env.library_directory = "out"
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "plugins/foo"
            project.resolve()

            assert _path(lib) == "build/out/plugins/libfoo.so"


class TestOutputPrefixAndSuffix:
    """They replace the toolchain's, and go on the file's own name too."""

    def test_an_empty_prefix_drops_lib(self, tmp_path, gcc_toolchain):
        project = _project(tmp_path)
        with building_for(LINUX):
            env = project.Environment(toolchain=gcc_toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "plugins/foo"
            lib.output_prefix = ""
            lib.output_suffix = ".plugin"
            project.resolve()

            assert _path(lib) == "build/plugins/foo.plugin"

    def test_a_directory_in_the_prefix_nests_below_the_names(
        self, tmp_path, gcc_toolchain
    ):
        """The prefix is text in front of the file's name, as it always was."""
        project = _project(tmp_path)
        with building_for(LINUX):
            env = project.Environment(toolchain=gcc_toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "lib/foo"
            lib.output_prefix = "debug/lib"
            project.resolve()

            assert _path(lib) == "build/lib/debug/libfoo.so"

    def test_a_directory_in_the_prefix_alone_is_unchanged(
        self, tmp_path, gcc_toolchain
    ):
        project = _project(tmp_path)
        with building_for(LINUX):
            env = project.Environment(toolchain=gcc_toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_prefix = "debug/lib"
            project.resolve()

            assert _path(lib) == "build/debug/libfoo.so"


class TestTheLibraryNamesItself:
    """install_name and SONAME carry the file's name, not its directory."""

    @pytest.mark.parametrize(
        ("triple", "basename"),
        [(LINUX, "libfoo.so"), (MACOS, "libfoo.dylib")],
    )
    def test_out_basename(self, tmp_path, gcc_toolchain, triple, basename):
        project = _project(tmp_path)
        with building_for(triple):
            env = project.Environment(toolchain=gcc_toolchain)
            lib = project.SharedLibrary("foo", env, sources=["lib.c"])
            lib.output_name = "lib/foo"
            NinjaGenerator().generate(project)
            BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert f"build lib/{basename}:" in content
        assert f"out_basename = {basename}" in content


class TestWasm:
    @pytest.fixture
    def emscripten(self):
        from pcons.toolchains.emscripten import (
            EmccArchiver,
            EmccCCompiler,
            EmccCxxCompiler,
            EmccLinker,
            EmscriptenToolchain,
        )

        toolchain = EmscriptenToolchain()
        toolchain._tools = {
            "cc": EmccCCompiler(),
            "cxx": EmccCxxCompiler(),
            "ar": EmccArchiver(),
            "link": EmccLinker(),
        }
        toolchain._configured = True
        return toolchain

    def test_a_program_and_its_wasm(self, tmp_path, emscripten):
        project = _project(tmp_path)
        env = project.Environment(toolchain=emscripten)
        app = project.Program("app", env, sources=["main.c"])
        app.output_name = "web/app"
        project.resolve()

        paths = {node.path.as_posix() for node in app.output_nodes}
        assert paths == {"build/web/app.js", "build/web/app.wasm"}

    def test_a_static_library(self, tmp_path, emscripten):
        project = _project(tmp_path)
        env = project.Environment(toolchain=emscripten)
        lib = project.StaticLibrary("foo", env, sources=["lib.c"])
        lib.output_name = "lib/foo"
        project.resolve()

        assert _path(lib) == "build/lib/libfoo.a"

    def test_the_toolchain_helpers(self, emscripten):
        assert emscripten.get_program_name("web/app") == "web/app.js"
        assert emscripten.get_static_library_name("lib/foo") == "lib/libfoo.a"


class TestNamingHelpers:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("foo", "libfoo.so"),
            ("lib/foo", "lib/libfoo.so"),
            ("a/b/foo", "a/b/libfoo.so"),
            ("lib\\foo", "lib/libfoo.so"),
            ("/opt/lib/foo", "/opt/lib/libfoo.so"),
        ],
    )
    def test_affix_filename(self, name, expected):
        assert affix_filename(name, "lib", ".so") == expected

    def test_last_component(self):
        assert last_component("a/b\\c.dll") == "c.dll"
        assert last_component("c.dll") == "c.dll"

    def test_platform_names(self):
        plat = get_platform()
        assert plat.shared_lib_name("lib/foo") == (
            f"lib/{plat.shared_lib_prefix}foo{plat.shared_lib_suffix}"
        )
        assert plat.static_lib_name("lib/foo") == (
            f"lib/{plat.static_lib_prefix}foo{plat.static_lib_suffix}"
        )
        assert plat.exe_name("bin/app") == f"bin/app{plat.exe_suffix}"
