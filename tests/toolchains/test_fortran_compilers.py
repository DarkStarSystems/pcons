# SPDX-License-Identifier: MIT
"""What differs between the Fortran toolchains (gfortran, flang, ifx), and
what they share through FortranToolchain."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import pcons.toolchains.flang as flang_mod
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
from pcons.toolchains.unix import UnixToolchain
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
def test_fortran_sources(cls: type[FortranToolchainBase]) -> None:
    tc = cls()
    for ext in FORTRAN_EXTENSIONS:
        handler = tc.get_source_handler(ext)
        assert handler is not None and handler.tool_name == "fc"


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


@pytest.fixture
def program(test_project):  # noqa: F811
    """A program target, which link_needs reads its type from."""
    from pcons.core.target import Target

    return Target("app", target_type="program", env=test_project.Environment())


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_cxx_link_gets_the_fortran_runtime(
    cls: type[FortranToolchainBase], program
) -> None:
    tc = cls()
    needs = tc.link_needs(program, {"fortran"}, "cxx", program._env)
    # MSVC-ABI objects name their runtimes (/DEFAULTLIB) themselves.
    assert needs.libs == (() if tc.objects_name_runtimes else cls.RUNTIME_LIBS)


def test_fortran_link_gets_the_cxx_runtime(gcc_toolchain, program) -> None:
    """The C++ toolchain states its own runtime when another driver links
    its objects (an ifx Fortran main program, link_language "fortran")."""
    needs = gcc_toolchain.link_needs(program, {"cxx"}, "fortran", program._env)
    assert needs.libs in (("c++",), ("stdc++",))
    assert gcc_toolchain.link_needs(program, {"cxx"}, "cxx", program._env).libs == ()


@pytest.mark.parametrize("cls", TOOLCHAINS)
def test_its_own_driver_needs_no_runtime_named(
    cls: type[FortranToolchainBase], program
) -> None:
    tc = cls()
    needs = tc.link_needs(program, {"fortran"}, "fortran", program._env)
    assert needs.libs == ()
    if not tc.objects_name_runtimes:
        assert needs.libdirs == ()


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
    unix_mod.target_triple.cache_clear()
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


@pytest.mark.parametrize("cls", [GfortranToolchain, FlangToolchain])
def test_no_runtime_libdir_from_a_compiler_that_will_not_run(cls) -> None:
    def broken(cmd, **kwargs):
        raise OSError("no such file")

    with patch.object(subprocess, "check_output", broken):
        assert cls()._find_runtime_libdir("fc") is None


def test_no_flang_runtime_libdir_without_the_runtime(tmp_path) -> None:
    unix_mod.target_triple.cache_clear()
    (tmp_path / "lib" / "darwin").mkdir(parents=True)
    answers = {
        "-print-resource-dir": f"{tmp_path}\n",
        "-print-target-triple": "arm64-apple-darwin\n",
    }

    with patch.object(subprocess, "check_output", lambda cmd, **k: answers[cmd[1]]):
        assert FlangToolchain()._find_runtime_libdir("flang") is None


# =============================================================================
# Presets and variants
# =============================================================================


def _by_tool(contribs) -> dict[str, tuple[str, ...]]:
    """Each tool's flags, from *contribs* in order."""
    flags: dict[str, tuple[str, ...]] = {}
    for c in contribs:
        flags[c.tool] = flags.get(c.tool, ()) + c.flags
    return flags


def _feature(tc: FortranToolchainBase, name: str) -> dict[str, tuple[str, ...]] | None:
    """What *tc* puts on each tool for feature *name*: None when it's
    unavailable, {} when it's a deliberate no-op."""
    preset = tc.make_feature_preset(name)
    return None if preset is None else _by_tool(preset.contributions)


@pytest.fixture
def on_linux(monkeypatch):
    from pcons.configure import platform as platform_mod

    linux = platform_mod.Platform(
        os="linux",
        arch="x86_64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".so",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )
    monkeypatch.setattr(platform_mod, "_platform", linux)
    return linux


@pytest.mark.parametrize(
    "name", ["werror", "sanitize", "profile", "lto", "pthread", "coverage", "fast-math"]
)
def test_gfortran_realizes_what_gcc_does(name) -> None:
    spec = UnixToolchain.FEATURE_PRESETS[name]
    expected = {"fc": spec["compile_flags"], "link": spec.get("link_flags", [])}
    assert _feature(GfortranToolchain(), name) == {
        tool: tuple(flags) for tool, flags in expected.items() if flags
    }


def test_gfortran_leaves_out_what_does_nothing_for_fortran() -> None:
    tc = GfortranToolchain()
    assert _feature(tc, "warnings") == {"fc": ("-Wall", "-Wextra")}
    assert _feature(tc, "hardened") == {
        "fc": ("-fstack-protector-strong", "-fPIE"),
        "link": ("-pie", "-Wl,-z,relro,-z,now"),
    }
    assert _feature(tc, "openmp") == {"fc": ("-fopenmp",), "link": ("-fopenmp",)}


def test_flang_features(on_linux) -> None:
    tc = FlangToolchain()
    assert _feature(tc, "warnings") == {}
    assert _feature(tc, "werror") == {"fc": ("-Werror",)}
    assert _feature(tc, "lto") == {"fc": ("-flto",), "link": ("-flto",)}
    assert _feature(tc, "pthread") == {"link": ("-pthread",)}
    assert _feature(tc, "fast-math") == {
        "fc": ("-ffast-math",),
        "link": ("-ffast-math",),
    }
    assert _feature(tc, "openmp") == {"fc": ("-fopenmp",), "link": ("-fopenmp",)}
    for name in ("sanitize", "profile", "hardened", "coverage"):
        assert _feature(tc, name) is None, name


def test_flang_features_for_the_msvc_abi(on_windows) -> None:
    tc = FlangToolchain()
    assert _feature(tc, "lto") == {"fc": ("-flto",), "link": ("-flto", "-fuse-ld=lld")}
    assert _feature(tc, "profile") == {"link": ("-Wl,/PROFILE",)}
    assert _feature(tc, "pthread") is None
    assert _feature(tc, "warnings") == {}
    for name in ("sanitize", "hardened", "coverage"):
        assert _feature(tc, name) is None, name


def test_ifx_features() -> None:
    tc = IfxToolchain()
    assert _feature(tc, "warnings") == {"fc": ("-warn", "all")}
    assert _feature(tc, "werror") == {"fc": ("-warn", "errors")}
    # Address only: ifx drops every sanitizer given "address,undefined".
    assert _feature(tc, "sanitize") == {
        "fc": ("-fsanitize=address", "-fno-omit-frame-pointer"),
        "link": ("-fsanitize=address",),
    }
    assert _feature(tc, "profile") == {"fc": ("-pg", "-g"), "link": ("-pg",)}
    assert _feature(tc, "lto") == {"fc": ("-flto",), "link": ("-flto",)}
    assert _feature(tc, "hardened") == {
        "fc": ("-fstack-protector-strong", "-fPIE"),
        "link": ("-pie", "-Wl,-z,relro,-z,now"),
    }
    assert _feature(tc, "pthread") == {"fc": ("-pthread",), "link": ("-pthread",)}
    assert _feature(tc, "openmp") == {"fc": ("-qopenmp",), "link": ("-qopenmp",)}
    assert _feature(tc, "coverage") is None


def test_ifx_on_windows_features() -> None:
    tc = IfxMsvcToolchain()
    assert _feature(tc, "warnings") == {"fc": ("/warn:all",)}
    assert _feature(tc, "sanitize") == {
        "fc": ("/fsanitize=address",),
        "link": ("/INFERASANLIBS",),
    }
    assert _feature(tc, "profile") == {"link": ("/PROFILE",)}
    hardened = _feature(tc, "hardened")
    assert hardened is not None
    assert hardened["fc"] == ("/GS", "/guard:cf")
    assert sorted(hardened["link"]) == ["/DYNAMICBASE", "/NXCOMPAT", "/guard:cf"]
    assert _feature(tc, "fast-math") == {"fc": ("/fp:fast",)}
    assert _feature(tc, "openmp") == {"fc": ("/Qopenmp",)}
    for name in ("lto", "pthread", "coverage"):
        assert _feature(tc, name) is None, name


# =============================================================================
# Arch and cross targets
# =============================================================================


@pytest.mark.parametrize("cls", [GfortranToolchain, FlangToolchain, IfxToolchain])
def test_gnu_style_fortran_cant_retarget_the_cpu(cls, on_linux) -> None:
    tc = cls()
    with pytest.raises(ValueError, match=f"{tc.name} cannot retarget the CPU"):
        tc._arch_contributions("arm64")


def test_an_arch_a_fortran_toolchain_cant_build_raises(tmp_path, monkeypatch) -> None:
    """On macOS clang takes -arch; gfortran ignores it and would build
    for the host, so the environment raises."""
    from pcons import Project
    from pcons.configure import platform as platform_mod

    mac = platform_mod.Platform(
        os="darwin",
        arch="arm64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".dylib",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )
    monkeypatch.setattr(platform_mod, "_platform", mac)
    project = Project("arch", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("llvm").create_toolchain()
    )
    env.add_toolchain(toolchain_registry.get("gfortran").create_toolchain())
    with pytest.raises(ValueError, match="gfortran cannot retarget the CPU"):
        env.set_target_arch("x86_64")


@pytest.mark.parametrize("arch", ["x64", "x86_64", "amd64"])
def test_ifx_on_windows_builds_x64(arch) -> None:
    contribs = IfxMsvcToolchain()._arch_contributions(arch)
    assert [(c.tool, c.flags) for c in contribs] == [
        ("link", ("/MACHINE:X64",)),
        ("lib", ("/MACHINE:X64",)),
    ]


@pytest.mark.parametrize("arch", ["arm64", "x86"])
def test_ifx_on_windows_builds_x64_only(arch) -> None:
    with pytest.raises(ValueError, match="ifx builds x64 only"):
        IfxMsvcToolchain()._arch_contributions(arch)


def test_flang_retargets_fc_and_link() -> None:
    from pcons.toolchains.presets import CrossPreset

    cross = CrossPreset(
        name="linux-aarch64",
        arch="aarch64",
        triple="aarch64-linux-gnu",
        sysroot="/sysroots/aarch64",
        extra_compile_flags=("-mfoo",),
    )
    flags = _by_tool(FlangToolchain()._target_contributions(cross))
    for tool in ("fc", "link"):
        assert "--target=aarch64-linux-gnu" in flags[tool]
        assert "--sysroot=/sysroots/aarch64" in flags[tool]
    # A preset's extra_compile_flags are C-family flags: cc and cxx only.
    assert flags["cc"] == flags["cxx"] == ("-mfoo",)
    assert "-mfoo" not in flags["fc"]


def test_flang_takes_the_apple_sdk(monkeypatch) -> None:
    from pcons.toolchains.presets import CrossPreset

    monkeypatch.setattr(unix_mod, "apple_sdk_for_triple", lambda triple: "/SDKs/iOS")
    cross = CrossPreset(name="ios", arch="arm64", triple="arm64-apple-ios15.0")
    flags = _by_tool(FlangToolchain()._target_contributions(cross))
    for tool in ("fc", "link"):
        assert flags[tool] == ("--target=arm64-apple-ios15.0", "-isysroot", "/SDKs/iOS")
    assert "cc" not in flags and "cxx" not in flags


def _fortran_env(tmp_path, name: str):
    """An environment of gfortran, or of ifx with its Linux conventions
    whatever the host."""
    from pcons import Project

    if name == "ifx":
        toolchain = IfxToolchain()
        toolchain._tools = {
            "fc": IfxCompiler(),
            "ar": GccArchiver(),
            "link": IfxLinker(),
        }
        toolchain._configured = True
    else:
        toolchain = toolchain_registry.get(name).create_toolchain()
    project = Project("cross", root_dir=tmp_path, build_dir="build")
    return project.Environment(toolchain=toolchain)


def test_gfortran_needs_a_cross_binary(tmp_path, on_linux) -> None:
    from pcons.toolchains.presets import linux_cross

    env = _fortran_env(tmp_path, "gfortran")
    with pytest.raises(ValueError, match="gfortran selects targets by binary"):
        env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))


def test_gfortran_retargets_by_binary(tmp_path, on_linux) -> None:
    from pcons.toolchains.presets import CrossPreset

    env = _fortran_env(tmp_path, "gfortran")
    env.apply_cross_preset(
        CrossPreset(
            name="linux-aarch64",
            arch="aarch64",
            triple="aarch64-linux-gnu",
            tool_cmds={"fc": "aarch64-linux-gnu-gfortran"},
        )
    )
    assert env.fc.cmd == "aarch64-linux-gnu-gfortran"
    assert not any(str(f).startswith("--target") for f in env.fc.flags)


def test_ifx_has_no_cross_targets(tmp_path, on_linux) -> None:
    from pcons.toolchains.presets import linux_cross

    env = _fortran_env(tmp_path, "ifx")
    with pytest.raises(ValueError, match="ifx has no cross targets"):
        env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))


@pytest.mark.parametrize("name", ["gfortran", "ifx"])
def test_a_wasm_target_says_to_use_the_wasm_toolchain(tmp_path, on_linux, name) -> None:
    from pcons.toolchains.presets import CrossPreset

    env = _fortran_env(tmp_path, name)
    wasm = CrossPreset(name="wasm", arch="wasm32", triple="wasm32-unknown-emscripten")
    with pytest.raises(ValueError, match="targets WebAssembly"):
        env.apply_cross_preset(wasm)


def test_a_debug_link_writes_a_pdb_for_the_msvc_abi(on_windows) -> None:
    """flang (and clang) targeting MSVC write a PDB only when linking with -g."""
    tc = FlangToolchain()
    assert ("link", ("-g",)) in [
        (c.tool, c.flags) for c in tc._variant_contributions("debug")
    ]
    assert all(c.tool != "link" for c in tc._variant_contributions("release"))


def test_a_debug_link_is_unchanged_elsewhere(on_linux) -> None:
    assert all(
        c.tool != "link" for c in FlangToolchain()._variant_contributions("debug")
    )


def test_flang_minsizerel_has_no_os() -> None:
    # (On Windows a C runtime contribution follows.)
    contribution = FlangToolchain()._variant_contributions("minsizerel")[0]
    assert contribution.tool == "fc"
    assert "-Os" not in contribution.flags
    assert "-O2" in contribution.flags


def test_ifx_compiles_position_independent_code() -> None:
    """Every ifx object is position-independent, which ifx doesn't default
    to: PIC for a static library where shared libraries need it."""
    from pcons.configure.platform import get_platform
    from pcons.toolchains.gnu_common import needs_pic

    tc = IfxToolchain()
    assert tc.get_compile_flags_for_target_type("program") == ["-fPIE"]
    pic = needs_pic("static_library", get_platform())
    assert tc.get_compile_flags_for_target_type("static_library") == (
        ["-fPIC"] if pic else ["-fPIE"]
    )


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
    with _installed(*installed):
        assert isinstance(find_fortran_toolchain(), cls)


def test_a_preference_overrides_the_detection_order(monkeypatch) -> None:
    monkeypatch.delenv("FC", raising=False)
    with _installed("gfortran", "flang"):
        assert isinstance(find_fortran_toolchain(prefer=["flang"]), FlangToolchain)


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


def test_ifx_on_windows_is_msvc_style(program) -> None:
    fc_vars = IfxMsvcToolchain.COMPILER().default_vars()
    objcmd = fc_vars["objcmd"]
    assert fc_vars["cmd"] == "ifx"
    assert "/module:$fc.moddir" in objcmd
    assert "/c" in objcmd
    tc = IfxMsvcToolchain()
    assert tc.TOOL_NAMES == ("fc", "lib", "link")
    assert tc.objects_name_runtimes
    assert tc.link_needs(program, {"fortran"}, "cxx", program._env).libs == ()


def test_ifx_on_windows_variants_use_the_dll_c_runtime() -> None:
    tc = IfxMsvcToolchain()
    debug = tc._variant_contributions("debug")
    assert "/MDd" in debug[0].flags
    assert ("link", ("/DEBUG",)) in [(c.tool, c.flags) for c in debug]
    (release,) = tc._variant_contributions("release")
    assert "/MD" in release.flags
    with pytest.raises(ValueError, match="Unknown variant"):
        tc._variant_contributions("fastest-ever")


def test_an_added_ifx_keeps_msvcs_c_runtime(on_windows, tmp_path) -> None:
    """Each toolchain's default CRT is its own knob setting: ifx's /MD
    joins MSVC's rather than replacing it, and debug switches both."""
    from pcons import Project
    from pcons.toolchains.ifx import IfxMsvcCompiler
    from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker

    ifx = IfxMsvcToolchain()
    ifx._tools = {"fc": IfxMsvcCompiler(), "lib": MsvcLibrarian(), "link": MsvcLinker()}
    ifx._configured = True
    project = Project("crt", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("msvc").create_toolchain()
    )
    env.add_toolchain(ifx)
    assert "/MD" in env.cc.flags and "/MD" in env.fc.flags

    env.set_variant("debug")
    assert "/MDd" in env.cc.flags and "/MD" not in env.cc.flags
    assert "/MDd" in env.fc.flags and "/MD" not in env.fc.flags


def test_ifx_on_windows_finds_its_runtime_beside_bin(tmp_path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libifcoremd.lib").touch()
    ifx = tmp_path / "bin" / "ifx.exe"
    found = IfxMsvcToolchain()._find_runtime_libdir(str(ifx))
    assert found == str((tmp_path / "lib").resolve())
    elsewhere = tmp_path / "a" / "bin" / "ifx.exe"
    assert IfxMsvcToolchain()._find_runtime_libdir(str(elsewhere)) is None


def test_ifx_on_windows_preprocesses_capitalized_sources(on_windows, tmp_path) -> None:
    """Windows file names don't keep case, so ifx preprocesses .F90 only
    when told to; .f90 isn't preprocessed."""
    from pcons import Project
    from pcons.toolchains.ifx import IfxMsvcCompiler
    from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker

    ifx = IfxMsvcToolchain()
    ifx._tools = {"fc": IfxMsvcCompiler(), "lib": MsvcLibrarian(), "link": MsvcLinker()}
    ifx._configured = True
    (tmp_path / "pre.F90").write_text("program p\nend program\n")
    (tmp_path / "plain.f90").write_text("subroutine s()\nend subroutine\n")
    project = Project("fpp", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=ifx)
    prog = project.Program("app", env, sources=["pre.F90", "plain.f90"])
    project.resolve()

    command_vars = {
        node.path.name.split(".")[0]: node._build_info["command_var"]
        for node in prog.intermediate_nodes
    }
    assert command_vars == {"pre": "fppcmd", "plain": "objcmd"}
    assert "/fpp" in env.fc.fppcmd and "/fpp" not in env.fc.objcmd


def test_ifx_on_windows_links_console_programs(on_windows, program) -> None:
    """link.exe can't infer the subsystem: main is in Intel's runtime. ifx
    says its programs need a console entry point, and the linker writes
    it its own way, unless the project chose a subsystem."""
    from pcons.core.target import Target
    from pcons.toolchains.clang_cl import ClangClToolchain
    from pcons.toolchains.llvm import LlvmToolchain

    tc = IfxMsvcToolchain()
    prog = program
    needs = tc.link_needs(prog, {"fortran"}, "fortran", prog._env)
    assert needs.console
    lib = Target("lib", target_type="shared_library")
    assert not tc.link_needs(lib, {"fortran"}, "fortran", lib._env).console

    link_exe = ClangClToolchain()
    assert link_exe.realize_link_needs(needs, prog, []) == ["/SUBSYSTEM:CONSOLE"]
    assert link_exe.realize_link_needs(needs, prog, ["/SUBSYSTEM:WINDOWS"]) == []
    # clang targeting MSVC drives link.exe or lld-link through -Wl,
    # (LNK1561 without it, seen on Windows).
    clang = LlvmToolchain()
    assert clang.realize_link_needs(needs, prog, []) == ["-Wl,/SUBSYSTEM:CONSOLE"]


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


@contextlib.contextmanager
def _installed(*names: str):
    """Patch shutil.which so exactly *names* are on PATH, each a complete
    install (a flang finds its intrinsic modules)."""

    def which(name, *args, **kwargs):
        return f"/usr/bin/{name}" if Path(name).name in names else None

    with (
        patch("shutil.which", which),
        patch.object(flang_mod, "intrinsic_modules_problem", lambda flang: None),
    ):
        yield


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


def test_no_compatible_c_says_which_would_do(on_windows, monkeypatch) -> None:
    from pcons.toolchains import find_c_toolchain

    for var in ("CC", "CXX"):
        monkeypatch.delenv(var, raising=False)
    with _installed("clang-cl"), pytest.raises(RuntimeError) as err:
        find_c_toolchain(abi="mingw")
    assert "'mingw' ABI" in str(err.value)
    assert "gcc" in str(err.value)


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


def test_without_an_abi_an_unavailable_toolchain_is_just_unavailable(
    tmp_path, monkeypatch
) -> None:
    from pcons import Project

    if sys.platform == "win32":
        pytest.skip("on Windows every toolchain declares an ABI")
    project = Project("p", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=GfortranToolchain())
    with _installed(), pytest.raises(RuntimeError) as err:
        env.add_toolchain("flang")
    assert "not available" in str(err.value)
    assert "beside" not in str(err.value)


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


def test_msys2_clang64_compilers_are_mingw(on_windows, monkeypatch, program) -> None:
    from pcons.toolchains.llvm import LlvmToolchain

    _answer(monkeypatch, "x86_64-w64-windows-gnu")
    flang = FlangToolchain()
    assert LlvmToolchain().abi == flang.abi == "mingw"
    # Targeting MinGW, flang links its runtime by name, as on Linux, and
    # takes no MSVC C runtime flag.
    assert not flang.objects_name_runtimes
    assert flang.link_needs(program, {"fortran"}, "cxx", program._env).libs == (
        "flang_rt.runtime",
    )
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


def test_an_added_toolchain_realizes_the_variant_already_set(
    on_windows, tmp_path
) -> None:
    """set_variant() before add_toolchain(): the added toolchain gets that
    variant (flang's debug C runtime), not its own default, and the
    environment keeps its variant name."""
    from pcons import Project

    project = Project("v", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("llvm").create_toolchain()
    )
    env.set_variant("debug")
    env.add_toolchain(toolchain_registry.get("flang").create_toolchain())

    assert "-fms-runtime-lib=dll_dbg" in env.fc.flags
    assert "-fms-runtime-lib=dll" not in env.fc.flags
    assert "-O0" in env.fc.flags and "-O0" in env.cxx.flags
    assert env.variant == "debug"


def test_a_variant_keeps_to_each_toolchains_own_tools(on_windows, tmp_path) -> None:
    """flang drives the link beside clang-cl: clang-cl's debug variant
    compiles its sources with /Z7 but must not hand flang's GNU-style
    driver link.exe's /DEBUG, which flang reads as a file name."""
    from pcons import Project

    project = Project("v", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("flang").create_toolchain()
    )
    env.add_toolchain(toolchain_registry.get("clang-cl").create_toolchain())
    env.set_variant("debug")

    assert "/Z7" in env.cxx.flags
    assert "-g" in env.fc.flags
    assert "/DEBUG" not in env.link.flags


@pytest.mark.parametrize(
    ("triple", "implib"),
    [
        ("aarch64-linux-gnu", None),
        ("x86_64-w64-windows-gnu", None),
        ("x86_64-pc-windows-msvc", "answer.lib"),
    ],
)
def test_the_import_library_follows_the_platform_built_for(
    on_windows, tmp_path, triple, implib
) -> None:
    """On a Windows host, clang cross-linking a shared library for Linux or
    MinGW writes no import library; only an MSVC target's link does."""
    from pcons import Project
    from pcons.toolchains.presets import linux_cross

    (tmp_path / "answer.c").write_text("int answer(void) { return 42; }\n")
    project = Project("x", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("llvm").create_toolchain()
    )
    env.apply_cross_preset(linux_cross(triple))
    lib = project.SharedLibrary("answer", env, sources=["answer.c"])
    project.resolve()

    (node,) = lib.output_nodes
    outputs = node._build_info.get("outputs") or {}
    found = outputs.get("import_lib", {}).get("path")
    assert (found.name if found else None) == implib
    flags = [str(f) for f in node._build_info["context"].link_flags]
    assert any("/IMPLIB:" in f for f in flags) == (implib is not None)
    # link.exe can leave an unchanged import library older than its inputs,
    # so a link that writes one restats rather than relinking every build.
    assert bool(node._build_info.get("restat")) == (implib is not None)


def test_a_clone_keeps_each_toolchains_flags_to_its_own_tools(
    on_windows, tmp_path
) -> None:
    from pcons import Project

    project = Project("v", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("flang").create_toolchain()
    )
    env.add_toolchain(toolchain_registry.get("clang-cl").create_toolchain())
    debug = env.clone()
    debug.set_variant("debug")

    assert "/Z7" in debug.cxx.flags
    assert "/DEBUG" not in debug.link.flags


def test_sanitize_covers_both_compilers_and_the_link(
    tmp_path, monkeypatch, gcc_toolchain
) -> None:
    """With gfortran primary and gcc added, each compiler sanitizes its own
    sources and gfortran, which owns the link, links the sanitizer, once."""
    from pcons import Project

    monkeypatch.chdir(tmp_path)
    project = Project("s", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("gfortran").create_toolchain()
    )
    env.add_toolchain(gcc_toolchain)
    env.apply_preset("sanitize")

    assert "-fsanitize=address,undefined" in env.cxx.flags
    assert "-fsanitize=address,undefined" in env.fc.flags
    assert list(env.link.flags).count("-fsanitize=address,undefined") == 1


def test_a_flang_dll_is_linked_through_its_import_library(on_windows, tmp_path) -> None:
    """Targeting the MSVC ABI, flang's link writes answer.lib beside
    answer.dll, and that, not the DLL, is what link.exe takes."""
    from pcons import Project
    from pcons.core.subst import TargetPath

    (tmp_path / "answer.f90").write_text("subroutine answer()\nend subroutine\n")
    (tmp_path / "main.f90").write_text("program p\nend program\n")
    project = Project("dll", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("flang").create_toolchain()
    )
    lib = project.SharedLibrary("answer", env, sources=["answer.f90"])
    prog = project.Program("main", env, sources=["main.f90"]).link(lib)
    project.resolve()

    (dll,) = lib.output_nodes
    outputs = dll._build_info["outputs"]
    assert outputs["import_lib"]["path"].name == "answer.lib"
    assert TargetPath(prefix="-Wl,/IMPLIB:", index=1) in (
        dll._build_info["context"].link_flags
    )
    (exe,) = prog.output_nodes
    linked = [n.path.name for n in exe.explicit_deps]
    assert "answer.lib" in linked and "answer.dll" not in linked


def test_a_flang_dll_exports_through_a_def_file(on_windows, tmp_path) -> None:
    """flang ignores ``!DIR$ ATTRIBUTES DLLEXPORT``, so a DLL's exports
    come from exported_symbols, as a .def file the link reads."""
    from pcons import Project
    from pcons.core.subst import PathToken

    (tmp_path / "answer.f90").write_text("subroutine answer() bind(c)\nend\n")
    project = Project("dll", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("flang").create_toolchain()
    )
    lib = project.SharedLibrary("answer", env, sources=["answer.f90"])
    lib.set_option("exported_symbols", ["answer"])
    project.resolve()

    (dll,) = lib.output_nodes
    (token,) = [
        f for f in dll._build_info["context"].link_flags if isinstance(f, PathToken)
    ]
    assert token.prefix == "-Wl,/DEF:"
    written = tmp_path / "build" / token.path
    assert written.read_text() == "EXPORTS\n    answer\n"


def test_a_flang_without_intrinsic_modules_is_not_available(caplog) -> None:
    """Visual Studio's bundled flang.exe has no intrinsic modules."""
    complaint = "error: Source file 'iso_c_binding.mod' was not found"
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr=complaint + "\n")
    flang_mod.intrinsic_modules_problem.cache_clear()
    with (
        patch("shutil.which", lambda name, *a, **k: "/vs/flang.exe"),
        patch.object(subprocess, "run", lambda *a, **k: failed),
    ):
        assert not flang_mod._flang_is_available()
    flang_mod.intrinsic_modules_problem.cache_clear()
    assert "iso_c_binding.mod" in caplog.text


def test_the_missing_module_is_what_the_warning_says(caplog) -> None:
    """flang's first line only says there were errors; the reason is the
    one naming the module, without the probe file's path."""
    complaint = (
        "error: Semantic errors in C:/tmp/probe.f90\n"
        "C:/tmp/probe.f90:2:7: error: Cannot parse module file for module "
        "'iso_c_binding': Source file 'iso_c_binding.mod' was not found\n"
        "    use iso_c_binding\n"
    )
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr=complaint)
    flang_mod.intrinsic_modules_problem.cache_clear()
    with (
        patch("shutil.which", lambda name, *a, **k: "/vs/flang.exe"),
        patch.object(subprocess, "run", lambda *a, **k: failed),
    ):
        assert not flang_mod._flang_is_available()
    flang_mod.intrinsic_modules_problem.cache_clear()
    assert "(error: Cannot parse module file for module 'iso_c_binding'" in caplog.text
    assert "probe.f90" not in caplog.text


def test_a_complete_flang_is_available() -> None:
    passed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    flang_mod.intrinsic_modules_problem.cache_clear()
    with (
        patch("shutil.which", lambda name, *a, **k: "/opt/flang"),
        patch.object(subprocess, "run", lambda *a, **k: passed),
    ):
        assert flang_mod._flang_is_available()
    flang_mod.intrinsic_modules_problem.cache_clear()


def test_a_flang_that_will_not_run_says_why() -> None:
    def broken(*args, **kwargs):
        raise OSError("bad executable")

    flang_mod.intrinsic_modules_problem.cache_clear()
    with patch.object(subprocess, "run", broken):
        assert flang_mod.intrinsic_modules_problem("/x/flang") == "bad executable"
    flang_mod.intrinsic_modules_problem.cache_clear()


def test_a_silent_flang_failure_gives_its_exit_code() -> None:
    failed = subprocess.CompletedProcess([], 3, stdout="", stderr="")
    flang_mod.intrinsic_modules_problem.cache_clear()
    with patch.object(subprocess, "run", lambda *a, **k: failed):
        assert flang_mod.intrinsic_modules_problem("/x/flang") == "exit code 3"
    flang_mod.intrinsic_modules_problem.cache_clear()


def test_an_added_ifx_names_the_console_subsystem(on_windows, tmp_path) -> None:
    """A Fortran program beside clang-cl links with link.exe, and ifx (not
    the primary) supplies the /SUBSYSTEM link.exe can't infer: main is in
    Intel's runtime."""
    from pcons import Project
    from pcons.toolchains.ifx import IfxMsvcCompiler, IfxMsvcToolchain
    from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker

    ifx = IfxMsvcToolchain()
    ifx._tools = {"fc": IfxMsvcCompiler(), "lib": MsvcLibrarian(), "link": MsvcLinker()}
    ifx._configured = True
    (tmp_path / "hello.f90").write_text("program p\nend program\n")
    project = Project("sub", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("clang-cl").create_toolchain()
    )
    env.add_toolchain(ifx)
    prog = project.Program("hello", env, sources=["hello.f90"])
    project.resolve()

    (link,) = (n for n in prog.output_nodes if n.path.suffix == ".exe")
    assert "/SUBSYSTEM:CONSOLE" in link._build_info["context"].link_flags


# =============================================================================
# Configure-driven setup (toolchain.configure(Configure(...)))
# =============================================================================


@pytest.mark.parametrize(
    ("cls", "archiver"),
    [
        (GfortranToolchain, "ar"),
        (FlangToolchain, "ar"),
        (IfxToolchain, "ar"),
        (IfxMsvcToolchain, "lib"),
    ],
)
def test_configure_finds_each_tool(cls, archiver, on_windows, tmp_path) -> None:
    from pcons.configure.config import Configure, ProgramInfo

    config = Configure(build_dir=tmp_path)
    config.find_program = (  # type: ignore[method-assign]
        lambda *a, **k: ProgramInfo(path=Path("/opt/bin/tool"), version="1")
    )
    tc = cls()
    assert tc.configure(config)
    assert set(tc.tools) == {"fc", archiver, "link"}


def test_configure_fails_without_a_compiler(tmp_path) -> None:
    from pcons.configure.config import Configure

    config = Configure(build_dir=tmp_path)
    config.find_program = lambda *a, **k: None  # type: ignore[method-assign]
    assert not GfortranToolchain().configure(config)


def test_configure_needs_a_configure() -> None:
    assert not GfortranToolchain().configure(object())


def test_a_fortran_main_linked_as_cxx_names_the_console_subsystem(
    on_windows, tmp_path
) -> None:
    """C++ beside a Fortran main program links as C++, with link.exe; ifx
    still supplies the /SUBSYSTEM the Fortran main needs."""
    from pcons import Project
    from pcons.toolchains.ifx import IfxMsvcCompiler
    from pcons.toolchains.msvc import MsvcLibrarian, MsvcLinker

    ifx = IfxMsvcToolchain()
    ifx._tools = {"fc": IfxMsvcCompiler(), "lib": MsvcLibrarian(), "link": MsvcLinker()}
    ifx._configured = True
    (tmp_path / "main.f90").write_text("program p\nend program\n")
    (tmp_path / "helper.cpp").write_text('extern "C" void helper() {}\n')
    project = Project("mix", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("clang-cl").create_toolchain()
    )
    env.add_toolchain(ifx)
    prog = project.Program("app", env, sources=["main.f90", "helper.cpp"])
    project.resolve()

    (link,) = (n for n in prog.output_nodes if n.path.suffix == ".exe")
    assert "/SUBSYSTEM:CONSOLE" in link._build_info["context"].link_flags


def test_an_added_gnu_style_toolchain_adds_no_link_flags_to_link_exe(
    on_windows, tmp_path
) -> None:
    """flang beside clang-cl compiles a DLL's Fortran, but link.exe links
    it: flang's -Wl,/IMPLIB: is for its own driver, not link.exe."""
    from pcons import Project

    (tmp_path / "answer.f90").write_text("subroutine answer()\nend subroutine\n")
    project = Project("dll", root_dir=tmp_path, build_dir="build")
    env = project.Environment(
        toolchain=toolchain_registry.get("clang-cl").create_toolchain()
    )
    env.add_toolchain(toolchain_registry.get("flang").create_toolchain())
    lib = project.SharedLibrary("answer", env, sources=["answer.f90"])
    project.resolve()

    (dll,) = lib.output_nodes
    flags = dll._build_info["context"].link_flags
    assert not any("-Wl," in str(f) for f in flags)


def test_flang_links_frameworks_through_the_linker(tmp_path) -> None:
    """flang's driver takes neither -framework nor -F, so a framework and
    its directory go to the linker with -Wl, each as one token."""
    from pcons import Project
    from pcons.toolchains.flang import FlangCompiler, FlangLinker
    from pcons.toolchains.llvm import LlvmArchiver

    tc = FlangToolchain()
    tc._tools = {"fc": FlangCompiler(), "ar": LlvmArchiver(), "link": FlangLinker()}
    tc._configured = True
    env = Project("fw", root_dir=tmp_path).Environment(toolchain=tc)
    env.link.frameworks = ["Accelerate"]
    env.link.frameworkdirs = ["/opt/frameworks"]

    for name in ("progcmd", "sharedcmd"):
        command = [str(t) for t in env.subst_list(getattr(env.link, name))]
        assert command[-2:] == ["-Wl,-F/opt/frameworks", "-Wl,-framework,Accelerate"]
