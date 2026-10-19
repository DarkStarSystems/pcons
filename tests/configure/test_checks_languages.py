# SPDX-License-Identifier: MIT
"""Configure checks compile with each tool the way the tool declares."""

import shutil
from pathlib import Path

import pytest

from pcons import Project
from pcons.configure.checks import ToolChecks
from pcons.configure.config import Configure
from pcons.core.errors import PconsError
from pcons.toolchains.clang_cl import ClangClCxxCompiler
from pcons.toolchains.flang import FlangCompiler, _flang_is_available
from pcons.toolchains.gcc import GccArchiver, GccCCompiler
from pcons.toolchains.gfortran import (
    GfortranCompiler,
    GfortranLinker,
    GfortranToolchain,
)
from pcons.toolchains.ifx import IfxCompiler, IfxMsvcCompiler
from pcons.toolchains.msvc import MsvcCxxCompiler
from pcons.toolchains.swift import SwiftCompiler
from pcons.tools.cuda import CudaCompiler
from pcons.tools.tool import Probe


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (GccCCompiler(), Probe(".c", "int main(void) { return 0; }\n")),
        (GfortranCompiler(), Probe(".f90", "end\n")),
        (FlangCompiler(), Probe(".f90", "end\n")),
        (IfxCompiler(), Probe(".f90", "end\n", ("-diag-error=10006,10148",))),
        (
            IfxMsvcCompiler(),
            Probe(".f90", "end\n", ("/Qdiag-error:10006,10148",), msvc_style=True),
        ),
        (
            MsvcCxxCompiler(),
            Probe(".cpp", "int main(void) { return 0; }\n", ("/WX",), True),
        ),
        (
            ClangClCxxCompiler(),
            Probe(".cpp", "int main(void) { return 0; }\n", ("/WX",), True),
        ),
        (CudaCompiler(), Probe(".cu", "int main(void) { return 0; }\n", ())),
        (SwiftCompiler(), None),
    ],
    ids=lambda v: type(v).__name__,
)
def test_each_tool_declares_its_probe(tool, expected) -> None:
    assert tool.probe() == expected


def _fortran_checks(tmp_path: Path) -> ToolChecks:
    """ToolChecks for an environment's gfortran, needing no gfortran."""
    tc = GfortranToolchain()
    tc._tools = {
        "fc": GfortranCompiler(),
        "ar": GccArchiver(),
        "link": GfortranLinker(),
    }
    project = Project("probe", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=tc)
    return ToolChecks(Configure(build_dir=tmp_path / "build"), env, "fc")


def _capture_commands(monkeypatch) -> list[list[str]]:
    """Record each command a check runs, and report it compiled."""
    commands: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(cmd, **kwargs):
        commands.append(list(cmd))
        return _Result()

    monkeypatch.setattr("pcons.configure.checks.subprocess.run", run)
    return commands


def test_try_compile_writes_the_tools_own_source_suffix(tmp_path, monkeypatch):
    """A Fortran probe is a .f90 file, which gfortran compiles as Fortran:
    written as .cpp, it was compiled as C++ and every probe failed."""
    commands = _capture_commands(monkeypatch)
    checks = _fortran_checks(tmp_path)

    assert checks.try_compile("end\n").success

    assert commands[0][-1].endswith("check.f90")


def test_check_flag_compiles_the_tools_program_strictly(tmp_path, monkeypatch):
    commands = _capture_commands(monkeypatch)
    checks = _fortran_checks(tmp_path)

    assert checks.check_flag("-ffree-line-length-none").success

    assert commands[0][-2:] == ["-Werror", "-ffree-line-length-none"]


@pytest.mark.parametrize(
    "check",
    [
        lambda c: c.check_header("stdio.h"),
        lambda c: c.check_type("size_t"),
        lambda c: c.check_type_size("int"),
        lambda c: c.check_define("FOO"),
        lambda c: c.check_defines(["FOO"]),
        lambda c: c.check_function("printf"),
    ],
    ids=["header", "type", "type_size", "define", "defines", "function"],
)
def test_c_only_checks_refuse_a_fortran_tool(tmp_path, check) -> None:
    """These write C programs, which a Fortran compiler can't take; saying
    so beats reporting the header or type missing."""
    with pytest.raises(PconsError, match="compiles a C program"):
        check(_fortran_checks(tmp_path))


# As pcons finds them: a flang without its intrinsic modules (Visual
# Studio's) doesn't count.
_REAL_FORTRAN = [
    fc
    for fc, usable in (
        ("gfortran", lambda: shutil.which("gfortran") is not None),
        ("flang", _flang_is_available),
    )
    if usable()
]


@pytest.mark.skipif(not _REAL_FORTRAN, reason="needs gfortran or flang")
@pytest.mark.parametrize("compiler", _REAL_FORTRAN)
def test_a_real_fortran_compiler_answers_probes(tmp_path, compiler) -> None:
    checks = _fortran_checks(tmp_path)
    checks._env.fc.cmd = shutil.which(compiler)

    assert checks.try_compile(
        "use, intrinsic :: ieee_arithmetic, only : ieee_is_nan\n"
        "logical :: x\n"
        "x = ieee_is_nan(0.0)\n"
        "end\n"
    ).success
    assert not checks.try_compile("this is not Fortran\n").success
    assert checks.check_flag("-O2").success
    assert not checks.check_flag("-fno-such-option-at-all").success
