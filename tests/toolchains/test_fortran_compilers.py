# SPDX-License-Identifier: MIT
"""What differs between the Fortran toolchains (gfortran, flang, ifx), and
what they share through FortranToolchain."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pcons.toolchains.flang import FlangToolchain
from pcons.toolchains.fortran import (
    FORTRAN_EXTENSIONS,
    FortranToolchain,
    find_fortran_toolchain,
)
from pcons.toolchains.gfortran import GfortranToolchain
from pcons.toolchains.ifx import IfxToolchain
from pcons.tools.toolchain import toolchain_registry

TOOLCHAINS = [GfortranToolchain, FlangToolchain, IfxToolchain]


@pytest.mark.parametrize(
    ("alias", "cls"),
    [("gfortran", GfortranToolchain), ("flang", FlangToolchain), ("ifx", IfxToolchain)],
)
def test_registered_as_fortran(alias, cls) -> None:
    entry = toolchain_registry.get(alias)
    assert entry is not None
    assert entry.toolchain_class is cls
    assert entry.category == "fortran"


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_fortran_sources_and_priority(cls: type[FortranToolchain]) -> None:
    tc = cls()
    for ext in FORTRAN_EXTENSIONS:
        handler = tc.get_source_handler(ext)
        assert handler is not None and handler.tool_name == "fc"
    assert tc.language_priority["fortran"] > tc.language_priority["cxx"]


@pytest.mark.parametrize(
    ("cls", "cmd", "moddir_flag"),
    [
        (GfortranToolchain, "gfortran", "-J"),
        (FlangToolchain, "flang", "-module-dir"),
        (IfxToolchain, "ifx", "-module"),
    ],
)
def test_compile_and_link_commands(cls, cmd, moddir_flag) -> None:
    fc_vars = cls.COMPILER().default_vars()
    objcmd = fc_vars["objcmd"]
    assert fc_vars["cmd"] == cmd
    assert objcmd[objcmd.index(moddir_flag) + 1] == "$fc.moddir"
    # The module directory is also searched for modules from other targets.
    assert objcmd[objcmd.index("-I") + 1] == "$fc.moddir"
    assert cls.LINKER().default_vars()["cmd"] == cmd


# =============================================================================
# Mixed-language runtime injection
# =============================================================================


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_cxx_link_gets_the_fortran_runtime(cls: type[FortranToolchain]) -> None:
    libs = cls().get_runtime_libs("cxx", {"cxx", "fortran"})
    assert libs == list(cls.RUNTIME_LIBS)


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_fortran_link_gets_the_cxx_runtime(cls: type[FortranToolchain]) -> None:
    libs = cls().get_runtime_libs("fortran", {"cxx", "fortran"})
    assert libs in (["c++"], ["stdc++"])


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_single_language_links_need_nothing(cls: type[FortranToolchain]) -> None:
    tc = cls()
    assert tc.get_runtime_libs("fortran", {"fortran"}) == []
    assert tc.get_runtime_libdirs("fortran", {"fortran"}) == []


@pytest.mark.parametrize(
    ("cls", "probe"),
    [(GfortranToolchain, "libgfortran.a"), (IfxToolchain, "libifcore.a")],
)
def test_runtime_libdir_from_print_file_name(cls, probe, tmp_path) -> None:
    lib = tmp_path / "lib" / probe

    def fake(cmd, **kwargs):
        assert cmd[1] == f"-print-file-name={probe}"
        return f"{lib}\n"

    with patch.object(subprocess, "check_output", fake):
        assert cls()._find_runtime_libdir("fc") == str(lib.resolve().parent)


def test_runtime_libdir_unknown_when_the_compiler_cannot_find_it() -> None:
    # A compiler prints just the name back when it can't find the file.
    with patch.object(subprocess, "check_output", lambda cmd, **k: "libgfortran.a"):
        assert GfortranToolchain()._find_runtime_libdir("gfortran") is None


@pytest.mark.parametrize("subdir", ["aarch64-unknown-linux-gnu", "darwin"])
def test_flang_runtime_libdir_under_the_resource_dir(subdir, tmp_path) -> None:
    rt_dir = tmp_path / "lib" / subdir
    rt_dir.mkdir(parents=True)
    (rt_dir / "libflang_rt.runtime.a").touch()
    answers = {
        "-print-resource-dir": f"{tmp_path}\n",
        "-print-target-triple": "aarch64-unknown-linux-gnu\n",
    }

    with patch.object(subprocess, "check_output", lambda cmd, **k: answers[cmd[1]]):
        assert FlangToolchain()._find_runtime_libdir("flang") == str(rt_dir)


def test_the_runtime_libdir_comes_from_fc(monkeypatch, tmp_path) -> None:
    fc = tmp_path / "gfortran-15"
    fc.write_text("#!/bin/sh\n")
    fc.chmod(0o755)
    monkeypatch.setenv("FC", str(fc))
    seen: list[str] = []

    def fake(cmd, **kwargs):
        seen.append(cmd[0])
        return "libgfortran.a"

    with patch.object(subprocess, "check_output", fake):
        assert GfortranToolchain().runtime_libdir is None
    assert seen == [str(fc)]


# =============================================================================
# Presets and variants
# =============================================================================


def test_presets_are_per_compiler() -> None:
    assert FlangToolchain.FEATURE_PRESETS["warnings"]["compile_flags"] == ["-Wall"]
    assert IfxToolchain.FEATURE_PRESETS["warnings"]["compile_flags"] == [
        "-warn",
        "all",
    ]
    # Neither has gfortran's --coverage, nor inherits C presets.
    for cls in (FlangToolchain, IfxToolchain):
        assert "coverage" not in cls.FEATURE_PRESETS
        assert "sanitize" not in cls.FEATURE_PRESETS


def test_flang_minsizerel_has_no_os() -> None:
    (contribution,) = FlangToolchain()._variant_contributions("minsizerel")
    assert contribution.tool == "fc"
    assert "-Os" not in contribution.flags
    assert "-O2" in contribution.flags


def test_ifx_compiles_position_independent_code() -> None:
    tc = IfxToolchain()
    assert tc.get_compile_flags_for_target_type("program") == ["-fPIE"]
    assert tc.get_compile_flags_for_target_type("static_library") == ["-fPIE"]


# =============================================================================
# Auto-detection
# =============================================================================


@pytest.mark.parametrize(
    ("installed", "cls"),
    [
        ({"gfortran", "flang", "ifx"}, GfortranToolchain),
        ({"flang", "ifx"}, FlangToolchain),
        ({"ifx"}, IfxToolchain),
    ],
)
def test_detection_order(installed, cls, monkeypatch) -> None:
    monkeypatch.delenv("FC", raising=False)

    def which(name, *args, **kwargs):
        return f"/usr/bin/{name}" if Path(name).name in installed else None

    with patch("shutil.which", which):
        assert isinstance(find_fortran_toolchain(), cls)


def test_an_added_toolchain_gets_its_own_target_type_flags(
    tmp_path, monkeypatch, gcc_toolchain
) -> None:
    """ifx's -fPIE reaches its sources in a C++-primary program, not g++'s."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FC", raising=False)
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    (tmp_path / "sum.f90").write_text("subroutine s()\nend subroutine\n")
    project = Project("mixed", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    env.add_toolchain(toolchain_registry.get("ifx").create_toolchain())
    prog = project.Program("app", env, sources=["main.cpp", "sum.f90"])
    project.resolve()

    flags = {
        node.path.name: node._build_info["context"].flags
        for node in prog.intermediate_nodes
    }
    assert "-fPIE" in flags["sum.f90.o"]
    assert "-fPIE" not in flags["main.cpp.o"]
