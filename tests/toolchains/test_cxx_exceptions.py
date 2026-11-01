# SPDX-License-Identifier: MIT
"""env.cxx.set_exceptions(): how C++ exceptions are handled (#224).

cl.exe and clang-cl compile C++ without unwinding unless told, so their
environments start with standard C++ exceptions (/EHsc), as CMake's do. GCC
and Clang have them on already. One mode replaces another.
"""

from __future__ import annotations

import pytest

from pcons import Project
from pcons.core.errors import PconsValueError
from pcons.tools.toolchain import toolchain_registry


@pytest.fixture
def project(tmp_path) -> Project:
    return Project("exceptions", root_dir=tmp_path, build_dir="build")


def test_msvc_style_cxx_has_standard_exceptions(project, clang_cl_toolchain):
    env = project.Environment(toolchain=clang_cl_toolchain)

    assert "/EHsc" in env.cxx.flags
    assert "/EHsc" not in env.cc.flags
    (row,) = [r for r in env.cxx.explain().rows if r.token == "/EHsc"]
    assert row.source == "exceptions-standard"


@pytest.mark.parametrize(
    ("mode", "flags"), [("none", []), ("async", ["/EHa"]), ("standard", ["/EHsc"])]
)
def test_a_mode_replaces_the_default(project, clang_cl_toolchain, mode, flags):
    env = project.Environment(toolchain=clang_cl_toolchain)
    env.cxx.set_exceptions(mode)

    assert [f for f in env.cxx.flags if f.startswith("/EH")] == flags


@pytest.mark.parametrize(
    ("mode", "flags"), [("standard", []), ("none", ["-fno-exceptions"])]
)
def test_gcc_turns_exceptions_off(project, gcc_toolchain, mode, flags):
    env = project.Environment(toolchain=gcc_toolchain)
    env.cxx.set_exceptions("none")
    env.cxx.set_exceptions(mode)

    assert [f for f in env.cxx.flags if "exceptions" in f] == flags
    assert "-fno-exceptions" not in env.cc.flags


def test_gcc_has_no_async_exceptions(project, gcc_toolchain):
    env = project.Environment(toolchain=gcc_toolchain)
    with pytest.raises(PconsValueError, match="/EHa"):
        env.cxx.set_exceptions("async")


def test_an_unknown_mode_raises(project, gcc_toolchain):
    env = project.Environment(toolchain=gcc_toolchain)
    with pytest.raises(PconsValueError, match="'standard', 'none', 'async'"):
        env.cxx.set_exceptions("off")


def _create(name: str):
    entry = toolchain_registry.get(name)
    assert entry is not None
    return entry.create_toolchain()


@pytest.mark.parametrize("set_first", [True, False])
def test_adding_a_toolchain_keeps_the_mode(project, set_first):
    """Adding flang beside clang-cl, before or after the mode is set, leaves
    the C++ compiler as the script set it."""
    env = project.Environment(toolchain=_create("clang-cl"))
    if set_first:
        env.cxx.set_exceptions("none")
    env.add_toolchain(_create("flang"))
    if not set_first:
        env.cxx.set_exceptions("none")

    assert not [f for f in env.cxx.flags if f.startswith("/EH")]


def test_a_toolchain_without_cxx_has_no_exception_mode():
    """It leaves the setting to the toolchain that compiles C++."""
    assert _create("cython").make_cxx_exceptions_preset("none") is None
