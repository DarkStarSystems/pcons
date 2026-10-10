# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.gcc."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcons.configure.platform import Platform
from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.subst import PathToken
from pcons.toolchains.gcc import (
    GccArchiver,
    GccCCompiler,
    GccCxxCompiler,
    GccLinker,
    GccToolchain,
)


class TestGccIsAvailable:
    """`_gcc_is_available` must reject the Apple Clang `gcc` shim on macOS."""

    def test_rejects_clang_shim(self, monkeypatch):
        from pcons.toolchains import gcc

        monkeypatch.setattr(gcc.shutil, "which", lambda _cmd: "/usr/bin/gcc")
        monkeypatch.setattr(
            gcc.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                stdout="Apple clang version 17.0.0 (clang-1700.0.13.3)\n",
                returncode=0,
            ),
        )
        assert gcc._gcc_is_available() is False

    def test_accepts_real_gcc(self, monkeypatch):
        from pcons.toolchains import gcc

        monkeypatch.setattr(gcc.shutil, "which", lambda _cmd: "/usr/bin/gcc")
        monkeypatch.setattr(
            gcc.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                stdout="gcc (GCC) 14.2.1 20240805\n", returncode=0
            ),
        )
        assert gcc._gcc_is_available() is True

    def test_missing_gcc(self, monkeypatch):
        from pcons.toolchains import gcc

        monkeypatch.setattr(gcc.shutil, "which", lambda _cmd: None)
        assert gcc._gcc_is_available() is False

    def test_probe_failure_assumes_usable(self, monkeypatch):
        from pcons.toolchains import gcc

        def _boom(*_a, **_k):
            raise OSError("cannot exec")

        monkeypatch.setattr(gcc.shutil, "which", lambda _cmd: "/usr/bin/gcc")
        monkeypatch.setattr(gcc.subprocess, "run", _boom)
        assert gcc._gcc_is_available() is True


class TestGccCCompiler:
    def test_creation(self):
        cc = GccCCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_default_vars(self):
        cc = GccCCompiler()
        vars = cc.default_vars()
        assert vars["cmd"] == "gcc"
        assert vars["flags"] == []
        assert vars["includes"] == []
        assert vars["defines"] == []
        assert "objcmd" in vars
        assert "$cc.cmd" in vars["objcmd"]

    def test_builders(self):
        cc = GccCCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert obj_builder.name == "Object"
        assert ".c" in obj_builder.src_suffixes


class TestGccCxxCompiler:
    def test_creation(self):
        cxx = GccCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = GccCxxCompiler()
        vars = cxx.default_vars()
        assert vars["cmd"] == "g++"
        assert "objcmd" in vars
        assert "$cxx.cmd" in vars["objcmd"]

    def test_builders(self):
        cxx = GccCxxCompiler()
        builders = cxx.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert ".cpp" in obj_builder.src_suffixes
        assert ".cxx" in obj_builder.src_suffixes
        assert ".cc" in obj_builder.src_suffixes


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX exec bits on PATH"
)


class TestGccArchiver:
    def test_creation(self):
        ar = GccArchiver()
        assert ar.name == "ar"

    def test_default_vars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATH", str(tmp_path))
        ar = GccArchiver()
        vars = ar.default_vars()
        assert vars["cmd"] == "ar"
        # flags is now a list (for consistency with subst)
        assert vars["flags"] == ["rcs"]
        assert "libcmd" in vars

    def test_builders(self):
        ar = GccArchiver()
        builders = ar.builders()
        assert "StaticLibrary" in builders
        lib_builder = builders["StaticLibrary"]
        assert lib_builder.name == "StaticLibrary"

    @posix_only
    def test_default_is_gcc_ar_on_path(self, monkeypatch, tmp_path):
        gcc_ar = _executable(tmp_path, "gcc-ar")
        monkeypatch.setenv("PATH", str(tmp_path))
        assert GccArchiver().default_vars()["cmd"] == str(gcc_ar)


class TestGccArchiverFor:
    """gcc_archiver_for: the gcc-ar that belongs to a driver (#214)."""

    @pytest.fixture(autouse=True)
    def _empty_path(self, monkeypatch, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))

    @pytest.mark.parametrize(
        ("driver", "archiver"),
        [
            ("gcc", "gcc-ar"),
            ("g++-16", "gcc-ar-16"),
            ("x86_64-pc-linux-gnu-g++-16", "x86_64-pc-linux-gnu-gcc-ar-16"),
            ("gcc-mp-14", "gcc-ar-mp-14"),
            ("cc", "gcc-ar"),
            ("c++", "gcc-ar"),
            ("x86_64-w64-mingw32-gcc.exe", "x86_64-w64-mingw32-gcc-ar.exe"),
        ],
    )
    def test_sibling_keeps_prefix_and_suffix(self, tmp_path, driver, archiver):
        from pcons.toolchains.gcc import gcc_archiver_for

        expected = _executable(tmp_path, archiver)
        assert gcc_archiver_for(str(tmp_path / driver)) == str(expected)

    def test_prefixed_driver_falls_back_to_target_ar(self, tmp_path):
        from pcons.toolchains.gcc import gcc_archiver_for

        target_ar = _executable(tmp_path, "arm-none-eabi-ar")
        driver = tmp_path / "arm-none-eabi-gcc"
        assert gcc_archiver_for(str(driver)) == str(target_ar)

    def test_not_a_gcc_driver(self, tmp_path):
        from pcons.toolchains.gcc import gcc_archiver_for

        _executable(tmp_path, "gcc-ar")
        for driver in ("clang", "clang++-18", "ccache", "nvcc"):
            assert gcc_archiver_for(str(tmp_path / driver)) is None

    def test_none_without_a_gcc_ar(self, tmp_path):
        from pcons.toolchains.gcc import gcc_archiver_for

        assert gcc_archiver_for(str(tmp_path / "gcc-14")) is None

    @posix_only
    def test_ccache_driver_finds_gcc_ar_on_path(self, monkeypatch, tmp_path):
        from pcons.toolchains.gcc import gcc_archiver_for

        ccache_dir = tmp_path / "ccache"
        bin_dir = tmp_path / "bin"
        ccache_dir.mkdir()
        bin_dir.mkdir()
        gcc_ar = _executable(bin_dir, "gcc-ar-14")
        monkeypatch.setenv("PATH", str(bin_dir))
        assert gcc_archiver_for(str(ccache_dir / "gcc-14")) == str(gcc_ar)


class TestGccLinker:
    def test_creation(self):
        link = GccLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = GccLinker()
        vars = link.default_vars()
        assert vars["cmd"] == "gcc"
        assert vars["flags"] == []
        assert vars["libs"] == []
        assert vars["libdirs"] == []
        assert "progcmd" in vars
        assert "sharedcmd" in vars

    def test_builders(self):
        link = GccLinker()
        builders = link.builders()
        assert "Program" in builders
        assert "SharedLibrary" in builders


class TestGccConfigure:
    """Each tool's configure() delegates to _find_tool_config."""

    @pytest.mark.parametrize(
        "cls", [GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker]
    )
    def test_configure_finds_program(self, cls, tmp_path):
        from pcons.configure.config import Configure, ProgramInfo

        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda *a, **k: ProgramInfo(path=Path("/usr/bin/tool"), version="1.0")
        )
        cfg = cls().configure(config)
        assert cfg is not None
        assert cfg.cmd == str(Path("/usr/bin/tool"))

    @pytest.mark.parametrize(
        "cls", [GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker]
    )
    def test_configure_returns_none_when_missing(self, cls, tmp_path):
        from pcons.configure.config import Configure

        config = Configure(build_dir=tmp_path)
        config.find_program = lambda *a, **k: None  # type: ignore[method-assign]
        assert cls().configure(config) is None


class TestGccToolchain:
    def test_creation(self):
        tc = GccToolchain()
        assert tc.name == "gcc"

    def test_tools_empty_before_configure(self):
        tc = GccToolchain()
        # Tools should be empty before configure
        assert tc.tools == {}


class TestGccSourceHandlers:
    """Tests for GCC source handler methods."""

    def test_source_handler_c(self):
        """Test that .c files are handled correctly."""
        from pcons.core.subst import TargetPath

        tc = GccToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "c"
        assert handler.object_suffix == ".o"
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_source_handler_cpp(self):
        """Test that .cpp files are handled correctly."""
        tc = GccToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx"

    def test_source_handler_s_lowercase(self):
        """Test that .s (lowercase) files are handled as preprocessed assembly."""
        tc = GccToolchain()
        handler = tc.get_source_handler(".s")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "asm"
        assert handler.object_suffix == ".o"
        # Preprocessed assembly has no dependency tracking
        assert handler.depfile is None
        assert handler.deps_style is None

    def test_source_handler_S_uppercase(self):
        """Test that .S (uppercase) files are handled as assembly needing preprocessing."""
        from pcons.core.subst import TargetPath

        tc = GccToolchain()
        handler = tc.get_source_handler(".S")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "asm-cpp"
        assert handler.object_suffix == ".o"
        # Assembly needing preprocessing has gcc-style dependency tracking
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_source_handler_objc(self):
        """Test that .m files are handled as Objective-C."""
        tc = GccToolchain()
        handler = tc.get_source_handler(".m")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "objc"

    def test_source_handler_objcxx(self):
        """Test that .mm files are handled as Objective-C++."""
        tc = GccToolchain()
        handler = tc.get_source_handler(".mm")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "objcxx"

    def test_source_handler_unknown(self):
        """Test that unknown suffixes return None."""
        tc = GccToolchain()
        handler = tc.get_source_handler(".xyz")
        assert handler is None


class TestGccCompileFlagsForTargetType:
    """Tests for get_compile_flags_for_target_type method."""

    def test_shared_library_linux(self, monkeypatch):
        """On Linux, shared libraries should get -fPIC."""
        # Mock the platform to be Linux
        linux_platform = Platform(
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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        tc = GccToolchain()
        flags = tc.get_compile_flags_for_target_type("shared_library")
        assert "-fPIC" in flags

    def test_shared_library_macos(self, monkeypatch):
        """On macOS, shared libraries don't need -fPIC (it's the default)."""
        # Mock the platform to be macOS
        macos_platform = Platform(
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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: macos_platform
        )

        tc = GccToolchain()
        flags = tc.get_compile_flags_for_target_type("shared_library")
        assert "-fPIC" not in flags
        assert flags == []

    def test_static_library_linux(self, monkeypatch):
        """Static libraries don't need -fPIC."""
        linux_platform = Platform(
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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        tc = GccToolchain()
        flags = tc.get_compile_flags_for_target_type("static_library")
        assert "-fPIC" not in flags
        assert flags == []

    def test_program_linux(self, monkeypatch):
        """Programs don't need -fPIC."""
        linux_platform = Platform(
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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        tc = GccToolchain()
        flags = tc.get_compile_flags_for_target_type("program")
        assert "-fPIC" not in flags
        assert flags == []

    def test_interface_target(self, monkeypatch):
        """Interface targets don't need special flags."""
        linux_platform = Platform(
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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        tc = GccToolchain()
        flags = tc.get_compile_flags_for_target_type("interface")
        assert flags == []


class TestGccModuleInterfaceSourceHandler:
    """Tests for GccToolchain.get_source_handler with C++20 module interfaces."""

    @pytest.mark.parametrize("suffix", [".cppm", ".ixx", ".cxxm", ".c++m"])
    def test_module_interface_suffixes(self, suffix: str) -> None:
        from pcons.core.subst import TargetPath

        tc = GccToolchain()
        handler = tc.get_source_handler(suffix)
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx_module"
        assert handler.object_suffix == ".o"
        assert handler.depfile == TargetPath(suffix=".d")


class TestGccModulesDepsTracking:
    """The per-target module pass: depfile handling and mapper wiring.

    A real (tiny) project: a target owning a module interface and a plain
    C++ TU, run through after_resolve the way the resolver does.
    """

    def _scanned_project(self, tmp_path, monkeypatch):
        from pcons.core.target import Target

        monkeypatch.chdir(tmp_path)
        tc = GccToolchain()
        project = Project("t", root_dir=tmp_path, build_dir="build")
        env = SimpleNamespace(
            cxx=SimpleNamespace(cmd="g++", flags=[], defines=[], dprefix="-D"),
            register_node=lambda _node: None,
            toolchains=(tc,),
        )

        def make_obj(path: str) -> FileNode:
            obj = FileNode(path)
            obj._build_info = {
                "env": env,
                "context": SimpleNamespace(flags=[], includes=[], defines=[]),
                "depfile": PathToken(path=path, path_type="build", suffix=".d"),
                "deps_style": "gcc",
            }
            return obj

        mod_obj = make_obj("build/obj/mod.cppm.o")
        cxx_obj = make_obj("build/obj/main.cpp.o")
        target = Target("hello", target_type="program", project=project)
        target._env = env  # type: ignore[assignment]
        target.intermediate_nodes.extend([mod_obj, cxx_obj])
        project._targets.append(target)

        # Std synthesis probes the compiler; not under test here.
        monkeypatch.setattr(tc, "_setup_std_modules", lambda *_a, **_k: ({}, None))

        source_obj_by_language = {
            "cxx_module": [(tmp_path / "src" / "mod.cppm", mod_obj)],
            "cxx": [(tmp_path / "src" / "main.cpp", cxx_obj)],
        }
        tc.after_resolve(project, source_obj_by_language)
        return project, target, mod_obj, cxx_obj

    def test_module_interfaces_drop_the_depfile(self, tmp_path, monkeypatch):
        """GCC's depfile for a module interface names the BMI as both target
        and prerequisite — a ninja cycle (#102). The interface compiles from
        a template without $cxx.depflags; its header tracking is an implicit
        dep on its own scan output, whose depfile covers the same reads."""
        _project, _target, mod_obj, _cxx_obj = self._scanned_project(
            tmp_path, monkeypatch
        )

        bi = mod_obj._build_info
        assert bi is not None
        assert bi["depfile"] is None
        assert bi["deps_style"] is None
        assert bi["command_var"] == "modifacecmd"
        assert any(
            str(d.path).endswith("mod.cppm.o.ddi") for d in mod_obj.implicit_deps
        )

    def test_non_module_cpp_keeps_its_depfile(self, tmp_path, monkeypatch):
        """Regular C++ TUs keep depfile/deps_style for #include tracking and
        compile with the mapper-aware template."""
        _project, _target, _mod_obj, cxx_obj = self._scanned_project(
            tmp_path, monkeypatch
        )

        bi = cxx_obj._build_info
        assert bi is not None
        assert bi["depfile"] is not None
        assert bi["deps_style"] == "gcc"
        assert bi["command_var"] == "modobjcmd"

    def test_every_scanned_tu_gets_a_mapper_reference(self, tmp_path, monkeypatch):
        _project, _target, mod_obj, cxx_obj = self._scanned_project(
            tmp_path, monkeypatch
        )

        for obj in (mod_obj, cxx_obj):
            bi = obj._build_info
            assert bi is not None
            ref = bi["vars"]["CXX_MODMAPREF"]
            assert ref.startswith("-fmodule-mapper=")
            assert ref.endswith(".modmap")

    def test_scanner_is_attached_to_the_target(self, tmp_path, monkeypatch):
        _project, target, _mod_obj, _cxx_obj = self._scanned_project(
            tmp_path, monkeypatch
        )

        assert len(target._scanners) == 1
        scanner = target._scanners[0]
        assert scanner.name == "cxx-modules"
        assert ".cppm" in scanner.source_suffixes


class TestFindGccStdModuleSource:
    """Tests for _find_gcc_std_module_source."""

    def test_resolves_std_cc_from_include_trace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        std_cc = tmp_path / "usr" / "include" / "c++" / "16.1.1" / "bits" / "std.cc"
        std_cc.parent.mkdir(parents=True)
        std_cc.write_text("// std module\n", encoding="utf-8")

        captured: dict[str, object] = {}

        def _fake_run(cmd: list[str], **kw: object) -> object:
            captured["cmd"] = cmd
            captured["input"] = kw.get("input")
            return type("R", (), {"stdout": "", "stderr": f". {std_cc}\n"})()

        monkeypatch.setattr("pcons.toolchains.gcc.subprocess.run", _fake_run)
        found = _find_gcc_std_module_source("g++", "std", [])
        assert captured["input"] == "#include <bits/std.cc>\n"
        assert captured["cmd"] == ["g++", "-E", "-x", "c++", "-", "-H"]
        assert found == std_cc

    def test_resolves_std_compat_cc_from_direct_include_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        compat_cc = (
            tmp_path / "usr" / "include" / "c++" / "16.1.1" / "bits" / "std.compat.cc"
        )
        compat_cc.parent.mkdir(parents=True)
        compat_cc.write_text("// std.compat module\n", encoding="utf-8")

        def _fake_run(_cmd: list[str], **_kw: object) -> object:
            return type("R", (), {"stdout": "", "stderr": f". {compat_cc}\n"})()

        monkeypatch.setattr("pcons.toolchains.gcc.subprocess.run", _fake_run)
        found = _find_gcc_std_module_source("g++", "std.compat", [])
        assert found == compat_cc

    def test_returns_none_when_compiler_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        def _fake_run(*_a: object, **_k: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr("pcons.toolchains.gcc.subprocess.run", _fake_run)
        assert _find_gcc_std_module_source("g++", "std", []) is None

    def test_returns_none_when_path_not_in_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd: list[str], **_kw: object) -> _Result:
            return _Result()

        monkeypatch.setattr("pcons.toolchains.gcc.subprocess.run", _fake_run)
        assert _find_gcc_std_module_source("g++", "std", []) is None

    def test_forwards_base_flags_to_direct_include_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from pcons.toolchains.gcc import _find_gcc_std_module_source

        std_cc = tmp_path / "usr" / "include" / "c++" / "16.1.1" / "bits" / "std.cc"
        std_cc.parent.mkdir(parents=True)
        std_cc.write_text("// std module\n", encoding="utf-8")

        captured: dict[str, object] = {}

        def _fake_run(cmd: list[str], **_kw: object) -> object:
            captured["cmd"] = cmd
            return type("R", (), {"stdout": "", "stderr": f". {std_cc}\n"})()

        monkeypatch.setattr("pcons.toolchains.gcc.subprocess.run", _fake_run)
        found = _find_gcc_std_module_source(
            "g++", "std", ["--sysroot=/x", "-std=c++23"]
        )
        assert found == std_cc
        assert captured["cmd"] == [
            "g++",
            "--sysroot=/x",
            "-std=c++23",
            "-E",
            "-x",
            "c++",
            "-",
            "-H",
        ]


class TestGccStdModuleFlagSpec:
    """Tests for _gcc_std_module_flag_spec and select_std_module_flags."""

    def test_carries_std_flag(self) -> None:
        from pcons.toolchains.cxx_module_scanner import select_std_module_flags
        from pcons.toolchains.gcc import _gcc_std_module_flag_spec

        flags = ["-std=c++23", "-O2", "-Wall"]
        result = select_std_module_flags(flags, _gcc_std_module_flag_spec())
        assert "-std=c++23" in result
        assert "-O2" not in result
        assert "-Wall" not in result

    def test_carries_march(self) -> None:
        from pcons.toolchains.cxx_module_scanner import select_std_module_flags
        from pcons.toolchains.gcc import _gcc_std_module_flag_spec

        flags = ["-std=c++20", "-march=native", "-Wextra"]
        result = select_std_module_flags(flags, _gcc_std_module_flag_spec())
        assert "-march=native" in result
        assert "-Wextra" not in result

    def test_carries_glibcxx_defines(self) -> None:
        from pcons.toolchains.cxx_module_scanner import select_std_module_flags
        from pcons.toolchains.gcc import _gcc_std_module_flag_spec

        flags = [
            "-std=c++23",
            "-D_GLIBCXX_DEBUG=1",
            "-DNDEBUG",
            "-D__GLIBCXX_SOMETHING=1",
        ]
        result = select_std_module_flags(flags, _gcc_std_module_flag_spec())
        assert "-D_GLIBCXX_DEBUG=1" in result
        assert "-D__GLIBCXX_SOMETHING=1" in result
        # NDEBUG is not a libstdc++ feature-test macro — don't carry it
        assert "-DNDEBUG" not in result

    def test_carries_exception_flags(self) -> None:
        from pcons.toolchains.cxx_module_scanner import select_std_module_flags
        from pcons.toolchains.gcc import _gcc_std_module_flag_spec

        flags = ["-fno-exceptions", "-fno-rtti", "-pthread"]
        result = select_std_module_flags(flags, _gcc_std_module_flag_spec())
        assert "-fno-exceptions" in result
        assert "-fno-rtti" in result
        assert "-pthread" in result
