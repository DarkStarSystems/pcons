# SPDX-License-Identifier: MIT
"""Which driver links a target, and with whose link flags.

A target is linked by the driver of its strongest language (C++ over
Fortran over C), and a toolchain whose driver takes over the link brings
its own realization of the variant and features with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons import Project
from pcons.configure import platform as platform_mod
from pcons.core.environment import Environment
from pcons.core.errors import PconsError
from pcons.tools.toolchain import toolchain_registry


@pytest.fixture(autouse=True)
def on_linux(monkeypatch):
    from pcons.toolchains import fortran as fortran_mod
    from pcons.toolchains import unix as unix_mod

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
    monkeypatch.setattr(unix_mod, "target_triple", lambda compiler: None)
    monkeypatch.setattr(
        unix_mod.UnixToolchain,
        "_compiler_is_apple_clang",
        lambda self, tool="cxx": False,
    )
    monkeypatch.setattr(
        fortran_mod.FortranToolchainBase,
        "runtime_libdir",
        property(lambda self: None),
    )


def _project(tmp_path) -> Project:
    for name, text in {
        "main.f90": "program main\nend program\n",
        "helper.cpp": 'extern "C" void helper() {}\n',
        "util.c": "void util(void) {}\n",
    }.items():
        (tmp_path / name).write_text(text)
    return Project("drivers", root_dir=tmp_path, build_dir="build")


def _env(project: Project, primary: str, *added: str) -> Environment:
    def make(name: str):
        entry = toolchain_registry.get(name)
        assert entry is not None
        return entry.create_toolchain()

    env = project.Environment(toolchain=make(primary))
    for name in added:
        env.add_toolchain(make(name))
    return env


def _link(target) -> tuple[str, list[str], list[str]]:
    """The program a target links with, its link flags and libraries."""
    (node,) = target.output_nodes
    info = node._build_info
    overrides = info["context"].get_env_overrides()
    env = info["env"]
    cmd = overrides.get("cmd", env.link.cmd)
    flags = [str(f) for f in overrides.get("flags", env.link.flags)]
    libs = [str(lib) for lib in overrides.get("libs", env.link.libs)]
    return str(cmd).replace("\\", "/").rsplit("/", 1)[-1], flags, libs


def test_a_fortran_program_is_linked_by_the_fortran_driver(tmp_path) -> None:
    project = _project(tmp_path)
    env = _env(project, "gcc", "gfortran")
    prog = project.Program("prog", env, sources=["main.f90"])
    project.resolve()
    cmd, _, libs = _link(prog)
    assert cmd.startswith("gfortran")
    assert "gfortran" not in libs  # its own runtime, which it links itself


def test_cxx_leads_fortran_whichever_toolchain_is_primary(tmp_path) -> None:
    """gfortran primary, gcc added: a program with C++ and Fortran links
    with g++ and the Fortran runtime, as with gcc primary."""
    project = _project(tmp_path)
    env = _env(project, "gfortran", "gcc")
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    project.resolve()
    cmd, _, libs = _link(prog)
    assert cmd.startswith("g++")
    assert "gfortran" in libs and "stdc++" not in libs


def test_the_driver_brings_its_own_link_flags(tmp_path) -> None:
    """ifx linking its own program takes ifx's realization of the
    features (address-only ASan, -qopenmp), not gcc's."""
    from pcons.toolchains.gcc import GccArchiver
    from pcons.toolchains.ifx import IfxCompiler, IfxLinker, IfxToolchain

    ifx = IfxToolchain()
    ifx._tools = {"fc": IfxCompiler(), "ar": GccArchiver(), "link": IfxLinker()}
    ifx._configured = True
    project = _project(tmp_path)
    env = _env(project, "gcc")
    env.add_toolchain(ifx)
    env.apply_preset("sanitize")
    env.apply_preset("openmp")
    prog = project.Program("prog", env, sources=["main.f90"])
    mixed = project.Program("mixed", env, sources=["main.f90", "helper.cpp"])
    project.resolve()

    cmd, flags, _ = _link(prog)
    assert cmd.startswith("ifx")
    assert "-fsanitize=address" in flags and "-qopenmp" in flags
    assert "-fsanitize=address,undefined" not in flags and "-fopenmp" not in flags

    cmd, flags, _ = _link(mixed)
    assert cmd.startswith("g++")
    assert "-fsanitize=address,undefined" in flags and "-fopenmp" in flags


def test_link_language_overrides_the_order(tmp_path) -> None:
    """A Fortran main program with a C++ helper, built with ifx, needs
    ifx's driver for its main: link_language says so."""
    project = _project(tmp_path)
    env = _env(project, "gcc", "gfortran")
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    prog.link_language = "fortran"
    project.resolve()
    cmd, _, _ = _link(prog)
    assert cmd.startswith("gfortran")


def test_link_language_must_be_compiled_here(tmp_path) -> None:
    project = _project(tmp_path)
    env = _env(project, "gcc")
    prog = project.Program("prog", env, sources=["util.c"])
    prog.link_language = "fortran"
    with pytest.raises(PconsError, match="none of its environment's toolchains"):
        project.resolve()


def test_a_link_command_the_script_chose_is_kept(tmp_path) -> None:
    project = _project(tmp_path)
    env = _env(project, "gcc", "gfortran")
    env.link.cmd = "my-linker"
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    project.resolve()
    cmd, _, _ = _link(prog)
    assert cmd == "my-linker"


def test_a_driver_with_another_command_line_doesnt_take_over(tmp_path) -> None:
    """swiftc doesn't take the GNU link command line clang's link tool
    writes, so clang keeps a link led by Swift objects."""
    (tmp_path / "lib.swift").write_text("public func f() {}\n")
    project = _project(tmp_path)
    env = _env(project, "llvm", "swiftc")
    prog = project.Program("prog", env, sources=["lib.swift", "helper.cpp"])
    project.resolve()
    cmd, _, _ = _link(prog)
    assert cmd.startswith("clang")


# =============================================================================
# What the link pulls in: static libraries from anywhere, imported ones
# =============================================================================


def test_a_static_library_from_another_environment_brings_its_runtime(
    tmp_path,
) -> None:
    """A Fortran program linking a C++ archive built in a C++ environment
    gets the C++ runtime, which g++ would have added. (It failed with
    undefined std::string symbols before.)"""
    project = _project(tmp_path)
    cxx_env = project.Environment(
        toolchain=toolchain_registry.get("gcc").create_toolchain(), name="cxx"
    )
    f_env = project.Environment(
        toolchain=toolchain_registry.get("gfortran").create_toolchain(), name="f"
    )
    lib = project.StaticLibrary("helper", cxx_env, sources=["helper.cpp"])
    prog = project.Program("prog", f_env, sources=["main.f90"]).link(lib)
    project.resolve()
    cmd, _, libs = _link(prog)
    assert cmd.startswith("gfortran")
    assert "stdc++" in libs


def test_a_shared_library_brings_no_link_language(tmp_path) -> None:
    """A shared library is linked already, with its own runtime: a C
    program linking a C++ one links with the C driver."""
    project = _project(tmp_path)
    env = _env(project, "gcc")
    lib = project.SharedLibrary("helper", env, sources=["helper.cpp"])
    prog = project.Program("prog", env, sources=["util.c"]).link(lib)
    project.resolve()
    cmd, _, libs = _link(prog)
    assert cmd.startswith("gcc")
    assert "stdc++" not in libs


@pytest.mark.parametrize("languages, runtime", [(["cxx"], True), ([], False)])
def test_an_imported_library_declares_its_languages(
    tmp_path, languages, runtime
) -> None:
    """A package built from C++ needs the C++ runtime; with no C++
    toolchain in the environment, gfortran's driver knows it."""
    from pcons.packages.description import PackageDescription
    from pcons.packages.imported import ImportedTarget

    project = _project(tmp_path)
    env = _env(project, "gfortran")
    pkg = PackageDescription(name="cxxlib", libraries=["cxxlib"], languages=languages)
    prog = project.Program("prog", env, sources=["main.f90"])
    prog.link(ImportedTarget.from_package(pkg))
    project.resolve()
    _, _, libs = _link(prog)
    assert ("stdc++" in libs) == runtime


# =============================================================================
# A package's compile flags reach the compilers of its languages
# =============================================================================


def _compile_flags(target, suffix: str) -> list[str]:
    (obj,) = [n for n in target.intermediate_nodes if n.path.name.endswith(suffix)]
    info = obj._build_info
    overrides = info["context"].get_env_overrides()
    tool = getattr(info["env"], info["tool"])
    return [str(f) for f in overrides.get("flags", tool.flags)]


@pytest.mark.parametrize("use", [True, False], ids=["env.use", "linked"])
@pytest.mark.parametrize(
    "languages, on_cxx, on_fc",
    [([], True, False), (["fortran"], False, True)],
    ids=["undeclared", "fortran"],
)
def test_package_compile_flags_follow_its_languages(
    tmp_path, use, languages, on_cxx, on_fc
) -> None:
    """Undeclared, a package's flags are C-family flags, and -pthread
    must not reach gfortran; a Fortran package's reach only fc. Its
    include directories reach every compiler either way."""
    from pcons.packages.description import PackageDescription
    from pcons.packages.imported import ImportedTarget

    project = _project(tmp_path)
    env = _env(project, "gcc", "gfortran")
    pkg = PackageDescription(
        name="pkg",
        include_dirs=["/pkg/include"],
        compile_flags=["-pkg-flag"],
        languages=languages,
    )
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    if use:
        env.use(pkg)
    else:
        prog.link(ImportedTarget.from_package(pkg))
    project.resolve()
    assert ("-pkg-flag" in _compile_flags(prog, ".cpp.o")) == on_cxx
    assert ("-pkg-flag" in _compile_flags(prog, ".f90.o")) == on_fc


# =============================================================================
# Runtimes a feature's objects need from another driver
# =============================================================================


def test_gfortran_coverage_objects_need_gcov_from_clang(tmp_path) -> None:
    """clang++'s --coverage doesn't bring libgcov, which gfortran's
    --coverage objects need (checked on macOS and Linux)."""
    project = _project(tmp_path)
    env = _env(project, "llvm", "gfortran")
    env.apply_preset("coverage")
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    plain = project.Program("plain", env, sources=["main.f90"])
    project.resolve()
    assert "gcov" in _link(prog)[2]
    assert "gcov" not in _link(plain)[2]  # gfortran links its own


def test_ifx_openmp_objects_need_intels_runtime_from_gcc(tmp_path) -> None:
    from pcons.toolchains.gcc import GccArchiver
    from pcons.toolchains.ifx import IfxCompiler, IfxLinker, IfxToolchain

    ifx = IfxToolchain()
    ifx._tools = {"fc": IfxCompiler(), "ar": GccArchiver(), "link": IfxLinker()}
    ifx._configured = True
    project = _project(tmp_path)
    env = _env(project, "gcc")
    env.add_toolchain(ifx)
    env.apply_preset("openmp")
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    project.resolve()
    assert "iomp5" in _link(prog)[2]


@pytest.mark.parametrize(
    "variant, onone", [("release", False), ("debug", True), (None, True)]
)
def test_swift_objects_linked_by_clang_on_linux(
    tmp_path, monkeypatch, variant, onone
) -> None:
    """swiftrt.o registers the runtime metadata, the rpath finds the
    runtime, and the autolinked libraries come along, as swiftc's driver
    adds them (checked with Swift 6.2 in Docker). swiftSwiftOnoneSupport
    is for -Onone objects, swiftc's default."""
    from pcons.toolchains import swift as swift_mod

    monkeypatch.setattr(
        swift_mod.SwiftToolchain,
        "_target_info_json",
        lambda self: {
            "target": {"arch": "x86_64"},
            "paths": {
                "runtimeLibraryPaths": ["/usr/lib/swift/linux"],
                "runtimeResourcePath": "/usr/lib/swift",
            },
        },
    )
    (tmp_path / "lib.swift").write_text("public func f() {}\n")
    project = _project(tmp_path)
    env = _env(project, "llvm", "swiftc")
    if variant:
        env.set_variant(variant)
    lib = project.StaticLibrary("swiftlib", env, sources=["lib.swift"])
    prog = project.Program("prog", env, sources=["helper.cpp"]).link(lib)
    project.resolve()
    cmd, flags, libs = _link(prog)
    assert cmd.startswith("clang++")
    assert "/usr/lib/swift/linux/x86_64/swiftrt.o" in flags
    assert "-Wl,-rpath,/usr/lib/swift/linux" in flags
    assert {"swiftCore", "swift_Concurrency"} <= set(libs)
    assert ("swiftSwiftOnoneSupport" in libs) == onone


# =============================================================================
# From the review of the redesign
# =============================================================================


def test_fortran_and_c_link_with_the_c_driver(tmp_path) -> None:
    """C leads Fortran (Meson's order): a C driver takes the Fortran
    runtime as libraries, as a Fortran driver can't take C's sanitizer or
    coverage runtimes."""
    project = _project(tmp_path)
    env = _env(project, "gcc", "gfortran")
    prog = project.Program("prog", env, sources=["main.f90", "util.c"])
    project.resolve()
    cmd, _, libs = _link(prog)
    assert cmd.startswith("gcc")
    assert "gfortran" in libs


def test_cxx_objects_beside_swift_primary_need_the_cxx_runtime(
    tmp_path, monkeypatch
) -> None:
    """Swift inherits the C++ handlers, but clang's cxx compiles the .cpp,
    so its objects are clang's and need the C++ runtime under swiftc."""
    from pcons.toolchains import swift as swift_mod

    monkeypatch.setattr(swift_mod.SwiftToolchain, "_target_info_json", lambda self: {})
    (tmp_path / "main.swift").write_text("print(1)\n")
    project = _project(tmp_path)
    env = _env(project, "swiftc", "llvm")
    prog = project.Program("prog", env, sources=["main.swift", "helper.cpp"])
    project.resolve()
    (cpp,) = [n for n in prog.intermediate_nodes if n.path.name.endswith(".cpp.o")]
    assert cpp._build_info["toolchain"].name == "llvm"
    assert "stdc++" in _link(prog)[2]


def test_a_toolchain_left_out_of_a_preset_doesnt_take_over(tmp_path) -> None:
    """flang, left out of sanitize, would link the C helper's sanitized
    objects without their runtime: clang links instead, adding flang's
    runtime. A Fortran-only program has no such objects, so flang links it."""
    project = _project(tmp_path)
    env = _env(project, "llvm", "flang")
    env.apply_preset("sanitize", allow_partial=True)
    mixed = project.Program("mixed", env, sources=["main.f90", "util.c"])
    alone = project.Program("alone", env, sources=["main.f90"])
    project.resolve()

    cmd, flags, libs = _link(mixed)
    assert cmd.startswith("clang")
    assert any("-fsanitize" in f for f in flags)
    assert "flang_rt.runtime" in libs
    assert _link(alone)[0].startswith("flang")


def test_the_gcov_need_says_where_libgcov_is(tmp_path, monkeypatch) -> None:
    from pcons.toolchains.gfortran import GfortranToolchain

    monkeypatch.setattr(
        GfortranToolchain, "gcov_libdir", property(lambda self: "/gcc/lib")
    )
    project = _project(tmp_path)
    env = _env(project, "llvm", "gfortran")
    env.apply_preset("coverage")
    prog = project.Program("prog", env, sources=["main.f90", "helper.cpp"])
    project.resolve()
    (node,) = prog.output_nodes
    libdirs = node._build_info["context"].get_env_overrides()["libdirs"]
    assert Path("/gcc/lib") in [Path(str(getattr(d, "path", d))) for d in libdirs]


def test_link_language_and_a_left_out_driver_conflict(tmp_path) -> None:
    """Asked to link with flang, which allow_partial left out of sanitize,
    beside sanitized C objects: pcons can't do both, so it says so."""
    project = _project(tmp_path)
    env = _env(project, "llvm", "flang")
    env.apply_preset("sanitize", allow_partial=True)
    prog = project.Program("prog", env, sources=["main.f90", "util.c"])
    prog.link_language = "fortran"
    with pytest.raises(PconsError, match="left flang out"):
        project.resolve()
