# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.gcc."""

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


class TestGccArchiver:
    def test_creation(self):
        ar = GccArchiver()
        assert ar.name == "ar"

    def test_default_vars(self):
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
    def test_after_resolve_keeps_depfile_for_non_module_cpp(
        self, tmp_path, monkeypatch
    ):
        """Non-module C++ TUs must keep depfile/deps_style for #include tracking."""
        tc = GccToolchain()
        project = Project("test", root_dir=tmp_path, build_dir="build")

        env = SimpleNamespace(
            cxx=SimpleNamespace(cmd="g++", flags=[]),
            register_node=lambda _node: None,
        )

        cxx_obj = FileNode("build/obj/main.cpp.o")
        cxx_obj._build_info = {
            "env": env,
            "context": SimpleNamespace(flags=[], includes=[], defines=[]),
            "depfile": PathToken(
                path="build/obj/main.cpp.o", path_type="build", suffix=".d"
            ),
            "deps_style": "gcc",
        }

        source_obj_by_language = {
            "cxx": [(tmp_path / "src" / "main.cpp", cxx_obj)],
        }

        def fake_select_modules_scope(_source_obj_by_language):
            # Simulate modules mode where regular C++ TUs are processed even without
            # an explicit module-interface unit in this particular list.
            return ([], source_obj_by_language["cxx"])

        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.select_modules_scope",
            fake_select_modules_scope,
        )
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.scan_translation_units",
            lambda specs, scanner, scanner_style: [
                SimpleNamespace(
                    spec=s,
                    required_logical_names=set(),
                    is_module_provider=False,
                )
                for s in specs
            ],
        )
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.build_module_map",
            lambda _results, _mod_dir, _ext: {},
        )
        monkeypatch.setattr(
            "pcons.toolchains.cxx_module_scanner.write_dyndep_from_results",
            lambda _results, _module_map, _out: None,
        )
        monkeypatch.setattr(
            tc,
            "_inject_gcc_std_module_builds",
            lambda *_args, **_kwargs: {},
        )

        tc.after_resolve(project, source_obj_by_language)

        build_info = cxx_obj._build_info
        assert build_info is not None
        assert build_info["depfile"] is not None
        assert build_info["deps_style"] == "gcc"

    def test_regular_cpp_not_cxx_module(self) -> None:
        tc = GccToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.language == "cxx"

    def test_unknown_suffix_returns_none(self) -> None:
        tc = GccToolchain()
        assert tc.get_source_handler(".xyz") is None


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
