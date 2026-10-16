# SPDX-License-Identifier: MIT
"""What differs between the Fortran toolchains (gfortran, flang, ifx), and
what they share through FortranToolchain."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pcons.toolchains import unix as unix_mod
from pcons.toolchains.flang import FlangToolchain
from pcons.toolchains.fortran import (
    FORTRAN_EXTENSIONS,
    FortranToolchainBase,
    find_fortran_toolchain,
)
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.gfortran import GfortranToolchain
from pcons.toolchains.ifx import (
    IfxCompiler,
    IfxLinker,
    IfxMsvcToolchain,
    IfxToolchain,
)
from pcons.tools.toolchain import toolchain_registry

TOOLCHAINS = [GfortranToolchain, FlangToolchain, IfxToolchain, IfxMsvcToolchain]
# "ifx" is registered once per host, with that platform's conventions.
REGISTERED_IFX = IfxMsvcToolchain if sys.platform == "win32" else IfxToolchain


@pytest.mark.parametrize(
    ("alias", "cls"),
    [
        ("gfortran", GfortranToolchain),
        ("flang", FlangToolchain),
        ("ifx", REGISTERED_IFX),
    ],
)
def test_registered_as_fortran(alias, cls) -> None:
    entry = toolchain_registry.get(alias)
    assert entry is not None
    assert entry.toolchain_class is cls
    assert entry.category == "fortran"


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_fortran_sources_and_priority(cls: type[FortranToolchainBase]) -> None:
    tc = cls()
    for ext in FORTRAN_EXTENSIONS:
        handler = tc.get_source_handler(ext)
        assert handler is not None and handler.tool_name == "fc"
    assert tc.language_priority["fortran"] > tc.language_priority["cxx"]


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_c_and_cxx_sources_belong_to_the_c_toolchain(
    cls: type[FortranToolchainBase],
) -> None:
    """A Fortran primary must leave .c/.cpp to the C/C++ toolchain added
    beside it, whose conventions (clang-cl's .obj and /showIncludes, say)
    may not be GNU's."""
    tc = cls()
    for ext in (".c", ".cpp", ".cc", ".S"):
        assert tc.get_source_handler(ext) is None


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
def test_cxx_link_gets_the_fortran_runtime(cls: type[FortranToolchainBase]) -> None:
    tc = cls()
    libs = tc.get_runtime_libs("cxx", {"cxx", "fortran"})
    # MSVC-ABI objects name their runtimes (/DEFAULTLIB) themselves.
    assert libs == ([] if tc.objects_name_runtimes else list(cls.RUNTIME_LIBS))


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_fortran_link_gets_the_cxx_runtime(cls: type[FortranToolchainBase]) -> None:
    tc = cls()
    libs = tc.get_runtime_libs("fortran", {"cxx", "fortran"})
    if tc.objects_name_runtimes:
        assert libs == []
    else:
        assert libs in (["c++"], ["stdc++"])


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_single_language_links_need_nothing(
    cls: type[FortranToolchainBase],
) -> None:
    tc = cls()
    assert tc.get_runtime_libs("fortran", {"fortran"}) == []
    if not tc.objects_name_runtimes:
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
    # (On Windows a C runtime contribution follows.)
    contribution = FlangToolchain()._variant_contributions("minsizerel")[0]
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
        ({"ifx"}, REGISTERED_IFX),
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
    ifx = IfxToolchain()
    ifx._tools = {"fc": IfxCompiler(), "ar": GccArchiver(), "link": IfxLinker()}
    ifx._configured = True
    env.add_toolchain(ifx)
    prog = project.Program("app", env, sources=["main.cpp", "sum.f90"])
    project.resolve()

    # Keyed by source name: the object suffix is the host's.
    flags = {
        node.path.name.rsplit(".", 1)[0]: node._build_info["context"].flags
        for node in prog.intermediate_nodes
    }
    assert "-fPIE" in flags["sum.f90"]
    assert "-fPIE" not in flags["main.cpp"]


def test_a_variant_reaches_every_toolchain(tmp_path, monkeypatch, gcc_toolchain):
    """set_variant fans out to each toolchain; one's variant flags must not
    un-apply another's (they share the build_variant group)."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    project = Project("mixed", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    env.add_toolchain(toolchain_registry.get("gfortran").create_toolchain())
    env.set_variant("debug")
    assert "-g" in env.cxx.flags
    assert "-g" in env.fc.flags
    env.set_variant("release")
    assert "-g" not in env.cxx.flags and "-g" not in env.fc.flags
    assert "-O2" in env.cxx.flags and "-O2" in env.fc.flags


# =============================================================================
# Windows: flang's C runtime, and ifx as an MSVC-style toolchain
# =============================================================================


@pytest.fixture
def on_windows(monkeypatch):
    from pcons.configure import platform as platform_mod

    win = platform_mod.Platform(
        os="windows",
        arch="x86_64",
        is_64bit=True,
        exe_suffix=".exe",
        shared_lib_suffix=".dll",
        shared_lib_prefix="",
        static_lib_suffix=".lib",
        static_lib_prefix="",
        object_suffix=".obj",
    )
    monkeypatch.setattr(platform_mod, "_platform", win)
    # No compiler is asked its target: each toolchain's WINDOWS_ABI holds,
    # whatever the host's compilers would say. (Tests of the probe set
    # their own answer.)
    monkeypatch.setattr(unix_mod, "target_triple", lambda compiler: None)
    return win


def test_flang_on_windows_uses_the_dll_c_runtime(on_windows) -> None:
    """Like pcons' MSVC and clang-cl builds (/MD, /MDd), so mixed objects
    agree on one C runtime; and its objects name their runtimes."""
    tc = FlangToolchain()
    assert tc.objects_name_runtimes
    assert tc.get_object_suffix() == ".obj"
    crt = tc._variant_contributions("debug")[-1]
    assert crt.flags == ("-fms-runtime-lib=dll_dbg",)
    crt = tc._variant_contributions("release")[-1]
    assert crt.flags == ("-fms-runtime-lib=dll",)


def test_flang_elsewhere_names_its_runtime() -> None:
    if sys.platform == "win32":
        pytest.skip("the host is Windows")
    tc = FlangToolchain()
    assert not tc.objects_name_runtimes
    assert all(c.tool == "fc" for c in tc._variant_contributions("debug"))


def test_ifx_on_windows_is_msvc_style() -> None:
    fc_vars = IfxMsvcToolchain.COMPILER().default_vars()
    objcmd = fc_vars["objcmd"]
    assert fc_vars["cmd"] == "ifx"
    assert "/module:$fc.moddir" in objcmd
    assert "/c" in objcmd
    tc = IfxMsvcToolchain()
    assert tc.TOOL_NAMES == ("fc", "lib", "link")
    assert tc.objects_name_runtimes
    assert tc.get_runtime_libs("cxx", {"cxx", "fortran"}) == []


def test_ifx_on_windows_variants_use_the_dll_c_runtime() -> None:
    tc = IfxMsvcToolchain()
    debug = tc._variant_contributions("debug")
    assert "/MDd" in debug[0].flags
    assert ("link", ("/DEBUG",)) in [(c.tool, c.flags) for c in debug]
    (release,) = tc._variant_contributions("release")
    assert "/MD" in release.flags
    with pytest.raises(ValueError, match="Unknown variant"):
        tc._variant_contributions("fastest-ever")


def test_ifx_on_windows_finds_its_runtime_beside_bin(tmp_path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libifcoremd.lib").touch()
    ifx = tmp_path / "bin" / "ifx.exe"
    found = IfxMsvcToolchain()._find_runtime_libdir(str(ifx))
    assert found == str((tmp_path / "lib").resolve())
    elsewhere = tmp_path / "a" / "bin" / "ifx.exe"
    assert IfxMsvcToolchain()._find_runtime_libdir(str(elsewhere)) is None


def test_ifx_on_windows_links_console_programs(tmp_path, monkeypatch) -> None:
    """link.exe can't infer the subsystem: main is in Intel's runtime."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    project = Project("p", root_dir=tmp_path, build_dir="build")
    env = project.Environment()
    prog = project.Program("app", env)
    lib = project.StaticLibrary("lib", env)
    tc = IfxMsvcToolchain()
    assert tc.get_link_flags_for_target(prog, "app.exe", []) == ["/SUBSYSTEM:CONSOLE"]
    assert tc.get_link_flags_for_target(prog, "app.exe", ["/SUBSYSTEM:WINDOWS"]) == []
    assert tc.get_link_flags_for_target(lib, "lib.lib", []) == []


def test_a_fortran_only_program_links_with_the_fortran_driver(
    tmp_path, monkeypatch, gcc_toolchain
) -> None:
    """With C++ primary and Fortran added, a program of Fortran sources
    links with the Fortran driver, which brings the Fortran runtime."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.f90").write_text("program p\nend program\n")
    project = Project("fonly", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    env.add_toolchain(toolchain_registry.get("gfortran").create_toolchain())
    prog = project.Program("hello", env, sources=["hello.f90"])
    project.resolve()

    (link,) = prog.output_nodes
    assert link._build_info["context"].linker_cmd == env.fc.cmd


# =============================================================================
# ABI matching: what add_toolchain() may put beside the primary toolchain
# =============================================================================


def _installed(*names: str):
    """Patch shutil.which so exactly *names* are on PATH."""

    def which(name, *args, **kwargs):
        return f"/usr/bin/{name}" if Path(name).name in names else None

    return patch("shutil.which", which)


def test_abi_compatible() -> None:
    from pcons.tools.toolchain import abi_compatible

    assert abi_compatible("msvc", "msvc")
    assert not abi_compatible("mingw", "msvc")
    # Undeclared on either side links with anything.
    assert abi_compatible(None, "msvc")
    assert abi_compatible("mingw", None)


def test_windows_abis(on_windows) -> None:
    from pcons.toolchains.gcc import GccToolchain
    from pcons.toolchains.llvm import LlvmToolchain

    assert GccToolchain().abi == GfortranToolchain().abi == "mingw"
    assert LlvmToolchain().abi == FlangToolchain().abi == "msvc"
    assert IfxMsvcToolchain().abi == "msvc"
    assert toolchain_registry.get("clang-cl").create_toolchain().abi == "msvc"


def test_no_abis_off_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("the host is Windows")
    for cls in (GfortranToolchain, FlangToolchain, IfxToolchain):
        assert cls().abi is None


def test_msvc_cxx_gets_an_msvc_fortran(on_windows, monkeypatch) -> None:
    """C++-primary on MSVC: "fortran" finds flang, passing MinGW gfortran."""
    monkeypatch.delenv("FC", raising=False)
    with _installed("gfortran", "flang"):
        assert isinstance(find_fortran_toolchain(abi="msvc"), FlangToolchain)
        assert isinstance(find_fortran_toolchain(abi="mingw"), GfortranToolchain)


def test_no_compatible_fortran_says_which_would_do(on_windows, monkeypatch) -> None:
    monkeypatch.delenv("FC", raising=False)
    with _installed("gfortran"), pytest.raises(RuntimeError) as err:
        find_fortran_toolchain(abi="msvc")
    assert "'msvc' ABI" in str(err.value)
    assert "flang" in str(err.value)


def test_gfortran_gets_mingw_gcc(on_windows, monkeypatch) -> None:
    """Fortran-primary on MinGW: "c" finds gcc, passing clang-cl and clang."""
    from pcons.toolchains import find_c_toolchain
    from pcons.toolchains.gcc import GccToolchain

    for var in ("CC", "CXX"):
        monkeypatch.delenv(var, raising=False)
    # gcc's own check runs `gcc --version` to reject macOS's clang shim;
    # here availability is only what the patched PATH says.
    monkeypatch.setattr(toolchain_registry.get("gcc"), "is_available", None)
    with _installed("clang-cl", "clang", "gcc"):
        assert isinstance(find_c_toolchain(abi="mingw"), GccToolchain)


def test_an_incompatible_fc_is_an_error(on_windows, monkeypatch, tmp_path) -> None:
    """$FC is the user's explicit choice: not honorable, not overridden."""
    fc = tmp_path / "gfortran"
    fc.write_text("#!/bin/sh\n")
    fc.chmod(0o755)
    monkeypatch.setenv("FC", str(fc))
    with pytest.raises(RuntimeError, match=r"\$FC names .*'mingw' ABI"):
        find_fortran_toolchain(abi="msvc")


def test_an_incompatible_name_is_an_error(on_windows) -> None:
    with _installed("gfortran"), pytest.raises(RuntimeError, match="'mingw' ABI"):
        toolchain_registry.resolve("gfortran", abi="msvc")


def test_envs_keep_their_own_abis(on_windows, tmp_path, monkeypatch, gcc_toolchain):
    """Each environment matches its own primary toolchain only: a MinGW
    environment and an MSVC one can sit side by side in one project."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    project = Project("two", root_dir=tmp_path, build_dir="build")
    mingw = project.Environment(toolchain=gcc_toolchain)
    mingw.add_toolchain(toolchain_registry.get("gfortran").create_toolchain())
    msvc = project.Environment(
        toolchain=toolchain_registry.get("clang-cl").create_toolchain()
    )
    msvc.add_toolchain(toolchain_registry.get("flang").create_toolchain())
    assert [t.name for t in mingw.toolchains] == ["gcc", "gfortran"]
    assert [t.name for t in msvc.toolchains] == ["clang-cl", "flang"]

    # Within an environment, a mismatch is refused.
    with pytest.raises(ValueError, match="can't be linked together"):
        mingw.add_toolchain(toolchain_registry.get("flang").create_toolchain())
    with _installed("gfortran"), pytest.raises(RuntimeError, match="clang-cl"):
        msvc.add_toolchain("gfortran")


# =============================================================================
# A clang driver's ABI comes from its target triple
# =============================================================================


@pytest.mark.parametrize(
    ("triple", "abi"),
    [
        ("x86_64-pc-windows-msvc", "msvc"),
        ("aarch64-pc-windows-msvc", "msvc"),
        ("x86_64-w64-windows-gnu", "mingw"),
        ("x86_64-w64-mingw32", "mingw"),
        ("arm64-apple-darwin24.0.0", None),
    ],
)
def test_abi_from_triple(triple, abi) -> None:
    assert unix_mod.abi_from_triple(triple) == abi


def _answer(monkeypatch, triple: str | None) -> list[str]:
    """Make every compiler report *triple*; return the compilers asked."""
    asked: list[str] = []

    def probe(compiler: str) -> str | None:
        asked.append(compiler)
        return triple

    monkeypatch.setattr(unix_mod, "target_triple", probe)

    # And put clang and flang on PATH, so there is a compiler to ask. (A
    # path, as $FC or $CXX may give, is found when the file exists.)
    def which(name, *args, **kwargs):
        if Path(name).name in ("clang", "flang") or Path(name).is_file():
            return str(name)
        return None

    monkeypatch.setattr("shutil.which", which)
    return asked


def test_msys2_clang64_compilers_are_mingw(on_windows, monkeypatch) -> None:
    from pcons.toolchains.llvm import LlvmToolchain

    _answer(monkeypatch, "x86_64-w64-windows-gnu")
    flang = FlangToolchain()
    assert LlvmToolchain().abi == flang.abi == "mingw"
    # Targeting MinGW, flang links its runtime by name, as on Linux, and
    # takes no MSVC C runtime flag.
    assert not flang.objects_name_runtimes
    assert flang.get_runtime_libs("cxx", {"cxx", "fortran"}) == ["flang_rt.runtime"]
    assert all(
        "-fms-runtime-lib" not in flag
        for c in flang._variant_contributions("debug")
        for flag in c.flags
    )


def test_llvm_compilers_are_msvc(on_windows, monkeypatch) -> None:
    from pcons.toolchains.llvm import LlvmToolchain

    _answer(monkeypatch, "x86_64-pc-windows-msvc")
    assert LlvmToolchain().abi == FlangToolchain().abi == "msvc"
    assert FlangToolchain().objects_name_runtimes


def test_an_unanswered_probe_falls_back(on_windows, monkeypatch) -> None:
    from pcons.toolchains.llvm import LlvmToolchain

    _answer(monkeypatch, None)
    assert LlvmToolchain().abi == FlangToolchain().abi == "msvc"


def test_the_probe_asks_the_compiler_that_will_build(
    on_windows, monkeypatch, tmp_path
) -> None:
    """$FC and $CXX/$CC name the compilers that build, so they're asked."""
    from pcons.toolchains.llvm import LlvmToolchain

    fc = tmp_path / "flang-new"
    cxx = tmp_path / "clang++"
    for exe in (fc, cxx):
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setenv("FC", str(fc))
    monkeypatch.setenv("CXX", str(cxx))
    asked = _answer(monkeypatch, "x86_64-w64-windows-gnu")
    assert FlangToolchain().abi == LlvmToolchain().abi == "mingw"
    assert [Path(a).name for a in asked] == ["flang-new", "clang++"]


def test_off_windows_nothing_is_asked(monkeypatch) -> None:
    if sys.platform == "win32":
        pytest.skip("the host is Windows")
    from pcons.toolchains.llvm import LlvmToolchain

    asked = _answer(monkeypatch, "x86_64-w64-windows-gnu")
    assert LlvmToolchain().abi is None and FlangToolchain().abi is None
    assert asked == []
