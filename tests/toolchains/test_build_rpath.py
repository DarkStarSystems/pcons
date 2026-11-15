# SPDX-License-Identifier: MIT
"""Programs and shared libraries find the build's own shared libraries."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pcons import Project
from pcons.configure.platform import Platform
from pcons.core.target import Target
from pcons.toolchains.gcc import GccToolchain
from pcons.tools.compile_link import _build_library_dirs
from pcons.tools.toolchain import LinkNeeds


def _platform(os_name: str) -> Platform:
    return Platform(
        os=os_name,
        arch="x86_64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".so",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        ("darwin", ["-Wl,-rpath,@loader_path", "-Wl,-rpath,@loader_path/../lib"]),
        ("linux", ["-Wl,-rpath,$$ORIGIN", "-Wl,-rpath,$$ORIGIN/../lib"]),
        ("freebsd", ["-Wl,-rpath,$$ORIGIN", "-Wl,-rpath,$$ORIGIN/../lib"]),
        ("windows", []),
        ("wasi", []),
    ],
)
def test_relative_rpaths_are_written_for_the_loader(
    os_name, expected, test_project
) -> None:
    """Mach-O's loader reads @loader_path, ELF's $ORIGIN; a DLL is found
    beside its program, and wasm has no loader to tell."""
    tc = GccToolchain()
    target = Target("app", target_type="program")
    needs = LinkNeeds(relative_rpaths=(".", "../lib"))

    with patch.object(
        GccToolchain, "_target_platform", return_value=_platform(os_name)
    ):
        assert tc.realize_link_needs(needs, target, []) == expected


def test_an_rpath_the_script_gave_is_not_repeated(test_project) -> None:
    tc = GccToolchain()
    target = Target("app", target_type="program")
    needs = LinkNeeds(relative_rpaths=(".",))

    with patch.object(
        GccToolchain, "_target_platform", return_value=_platform("darwin")
    ):
        assert tc.realize_link_needs(needs, target, ["-Wl,-rpath,@loader_path"]) == []


def test_an_rpath_the_driver_gives_is_not_repeated(test_project) -> None:
    """GCC's driver passes @loader_path and its own library directories on
    Apple platforms; ld64 warns about a second copy."""
    tc = GccToolchain()
    target = Target("app", target_type="program")
    needs = LinkNeeds(relative_rpaths=(".", "../lib"), rpaths=("/gcc/lib", "/other"))

    with (
        patch.object(
            GccToolchain, "_target_platform", return_value=_platform("darwin")
        ),
        patch(
            "pcons.toolchains.unix._driver_rpaths",
            return_value=frozenset({"@loader_path", "/gcc/lib"}),
        ) as probe,
    ):
        flags = tc.realize_link_needs(needs, target, [], driver="gcc-16")
    probe.assert_called_once_with("gcc-16")
    assert flags == ["-Wl,-rpath,/other", "-Wl,-rpath,@loader_path/../lib"]


def test_driver_rpaths_come_from_what_it_would_run() -> None:
    from pcons.toolchains.unix import _driver_rpaths

    stderr = (
        "Using built-in specs.\n"
        ' "/usr/bin/ld" -o a.out -rpath "@loader_path" -rpath /opt/gcc/lib probe.o\n'
    )
    _driver_rpaths.cache_clear()
    with patch("pcons.toolchains.unix.subprocess.run") as run:
        run.return_value.stderr = stderr
        assert _driver_rpaths("gcc-x") == {"@loader_path", "/opt/gcc/lib"}
    _driver_rpaths.cache_clear()


@pytest.fixture
def libraries(tmp_path, gcc_toolchain):
    """A program in bin/ linking a shared library, which links another
    shared library and a static one."""
    for name in ("main", "a", "b", "s"):
        (tmp_path / f"{name}.c").write_text(f"int {name}_fn(void) {{ return 0; }}\n")
    project = Project("rp", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    libb = project.SharedLibrary("b", env, sources=["b.c"])
    libb.output_filename = "lib/libb.so"
    libs = project.StaticLibrary("s", env, sources=["s.c"])
    liba = project.SharedLibrary("a", env, sources=["a.c"])
    liba.link(libb, libs)
    app = project.Program("app", env, sources=["main.c"])
    app.output_name = "bin/app"
    app.link(liba)
    return project, app, liba


def test_a_link_states_where_the_shared_libraries_it_pulls_in_are(libraries):
    """Each shared library the link pulls in, transitively, relative to the
    linked file; a static library is in the link itself."""
    project, app, liba = libraries
    project.resolve()

    assert set(_build_library_dirs(app, app.output_nodes[0])) == {"..", "../lib"}
    assert _build_library_dirs(liba, liba.output_nodes[0]) == ("lib",)


def test_build_rpath_false_opts_out(libraries):
    project, app, _liba = libraries
    app.set_option("build_rpath", False)
    project.resolve()

    assert _build_library_dirs(app, app.output_nodes[0]) == ()
