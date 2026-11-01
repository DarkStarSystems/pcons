# SPDX-License-Identifier: MIT
"""Import libraries belong to the toolchain and its target, not to the host.

An MSVC-compatible link writes ``foo.lib`` beside ``foo.dll`` and its
dependents link that; a GNU-style link writes none and its dependents link
the DLL itself. Both answers have to hold when the build is cross-compiled,
which is what these tests pin: the host here is whatever runs them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Project
from pcons.core.errors import PconsValueError
from pcons.toolchains.presets import target_platform_for_triple
from tests.support import building_for


def _sources(tmp_path: Path) -> None:
    (tmp_path / "lib.c").write_text("int f(void) { return 1; }\n")
    (tmp_path / "main.c").write_text("int f(void); int main(void) { return f(); }\n")


def _outputs(target) -> dict:
    return target.output_nodes[0]._build_info.get("outputs", {})


def _link_inputs(target) -> set[str]:
    """The paths on the link command line ($in)."""
    return {node.path.as_posix() for node in target.output_nodes[0].explicit_deps}


def test_a_cross_msvc_build_writes_an_import_library(tmp_path, clang_cl_toolchain):
    """The DLL's dependents link foo.lib, on a host that has no link.exe."""
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-pc-windows-msvc"):
        env = project.Environment(toolchain=clang_cl_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["lib.c"])
        program = project.Program("app", env, sources=["main.c"]).link(shared)
        project.resolve()

        assert shared.output_nodes[0].path.as_posix() == "build/foo.dll"
        assert _outputs(shared)["import_lib"]["path"].as_posix() == "build/foo.lib"
        assert "build/foo.lib" in _link_inputs(program)
        assert "build/foo.dll" not in _link_inputs(program)


def test_a_cross_mingw_build_links_the_dll_itself(tmp_path, gcc_toolchain):
    """A GNU link writes no import library, so there is none to link."""
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-w64-mingw32"):
        env = project.Environment(toolchain=gcc_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["lib.c"])
        program = project.Program("app", env, sources=["main.c"]).link(shared)
        project.resolve()

        assert shared.output_nodes[0].path.as_posix() == "build/foo.dll"
        assert "import_lib" not in _outputs(shared)
        assert "build/foo.dll" in _link_inputs(program)


def test_an_msvc_import_library_follows_the_dlls_name(clang_cl_toolchain):
    """output_name, output_prefix and a subdirectory all carry over."""
    name = clang_cl_toolchain.get_import_library_name("mcu/libfoo.dll")

    assert name == "mcu/libfoo.lib"


def test_an_import_library_keeps_the_stem_output_filename_chose(
    tmp_path, clang_cl_toolchain
):
    """A plugin named outright still links against a .lib of that stem."""
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-pc-windows-msvc"):
        env = project.Environment(toolchain=clang_cl_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["lib.c"])
        shared.output_filename = "myplugin.ofx"
        project.resolve()

        assert shared.output_nodes[0].path.as_posix() == "build/myplugin.ofx"
        assert _outputs(shared)["import_lib"]["path"].as_posix() == "build/myplugin.lib"


def test_a_gnu_toolchain_writes_none_for_a_windows_target(gcc_toolchain):
    """The answer is the toolchain's, so a Windows target does not change it."""
    mingw = target_platform_for_triple("x86_64-w64-mingw32")

    assert gcc_toolchain.get_import_library_name("foo.dll", mingw) is None


def _link_flags(target) -> list[str]:
    return [str(f) for f in target.output_nodes[0]._build_info["context"].link_flags]


def test_a_dll_that_exports_nothing_declares_no_import_library(
    tmp_path, clang_cl_toolchain
):
    """link.exe writes none for it, so a declared one would never appear and
    the DLL would relink on every build (#249)."""
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-pc-windows-msvc"):
        env = project.Environment(toolchain=clang_cl_toolchain)
        strings = project.SharedLibrary("strings", env, sources=["lib.c"])
        strings.set_option("import_library", False)
        plugin = project.SharedLibrary("plugin", env, sources=["lib.c"])
        project.resolve()

        assert "import_lib" not in _outputs(strings)
        assert not any("/IMPLIB:" in f for f in _link_flags(strings))
        assert any("/IMPLIB:" in f for f in _link_flags(plugin))


def test_linking_a_dll_without_an_import_library_says_why(tmp_path, clang_cl_toolchain):
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-pc-windows-msvc"):
        env = project.Environment(toolchain=clang_cl_toolchain)
        strings = project.SharedLibrary("strings", env, sources=["lib.c"])
        strings.set_option("import_library", False)
        project.Program("app", env, sources=["main.c"]).link(strings)

        with pytest.raises(PconsValueError, match="no import library"):
            project.resolve()


def test_without_an_import_library_a_gnu_dll_links_as_before(tmp_path, gcc_toolchain):
    """The option only drops an import library the link would write."""
    _sources(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    with building_for("x86_64-w64-mingw32"):
        env = project.Environment(toolchain=gcc_toolchain)
        shared = project.SharedLibrary("foo", env, sources=["lib.c"])
        shared.set_option("import_library", False)
        program = project.Program("app", env, sources=["main.c"]).link(shared)
        project.resolve()

        assert "build/foo.dll" in _link_inputs(program)
