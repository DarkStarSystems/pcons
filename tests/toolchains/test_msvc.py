# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.msvc."""

from pathlib import Path

import pytest

from pcons.configure.platform import get_platform
from pcons.core.builder import MultiOutputBuilder, OutputGroup
from pcons.core.environment import Environment
from pcons.toolchains.msvc import (
    MsvcCompiler,
    MsvcLibrarian,
    MsvcLinker,
    MsvcToolchain,
)


class TestMsvcCompiler:
    def test_creation(self):
        cc = MsvcCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_creation_with_name(self):
        cxx = MsvcCompiler(name="cxx", language="cxx")
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cc = MsvcCompiler()
        vars = cc.default_vars()
        assert vars["cmd"] == "cl.exe"
        assert vars["flags"] == ["/nologo"]
        assert vars["includes"] == []
        assert vars["defines"] == []
        assert "objcmd" in vars
        # objcmd is now a list template
        objcmd = vars["objcmd"]
        assert isinstance(objcmd, list)
        assert "$cc.cmd" in objcmd
        assert "/c" in objcmd
        # Output uses $$out which becomes $out for ninja
        assert "/Fo$$out" in objcmd

    def test_depflags(self):
        cc = MsvcCompiler()
        vars = cc.default_vars()
        assert "depflags" in vars
        assert vars["depflags"] == ["/showIncludes"]
        # Verify depflags is in objcmd
        objcmd = vars["objcmd"]
        assert "$cc.depflags" in objcmd

    def test_builder_has_msvc_deps_style(self):
        cc = MsvcCompiler()
        builders = cc.builders()
        obj_builder = builders["Object"]
        # Create a mock environment and build a target to check build_info
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "cl.exe"
        env.cc.objcmd = ["cl.exe", "/c", "/Fo$$out", "$$in"]
        result = obj_builder(env, "test.obj", ["test.c"])
        assert len(result) == 1
        target = result[0]
        assert hasattr(target, "_build_info")
        assert target._build_info.get("deps_style") == "msvc"
        # MSVC doesn't use a depfile (uses stdout)
        assert target._build_info.get("depfile") is None

    def test_builders(self):
        cc = MsvcCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert obj_builder.name == "Object"
        assert ".c" in obj_builder.src_suffixes
        assert ".cpp" in obj_builder.src_suffixes
        assert ".obj" in obj_builder.target_suffixes


class TestMsvcLibrarian:
    def test_creation(self):
        lib = MsvcLibrarian()
        assert lib.name == "lib"

    def test_default_vars(self):
        lib = MsvcLibrarian()
        vars = lib.default_vars()
        assert vars["cmd"] == "lib.exe"
        assert vars["flags"] == ["/nologo"]
        assert "libcmd" in vars
        # libcmd is now a list template
        libcmd = vars["libcmd"]
        assert isinstance(libcmd, list)
        # Output uses $$out which becomes $out for ninja
        assert "/OUT:$$out" in libcmd

    def test_builders(self):
        lib = MsvcLibrarian()
        builders = lib.builders()
        assert "StaticLibrary" in builders
        lib_builder = builders["StaticLibrary"]
        assert ".obj" in lib_builder.src_suffixes
        assert ".lib" in lib_builder.target_suffixes
        assert lib_builder.name == "StaticLibrary"


class TestMsvcLinker:
    def test_creation(self):
        link = MsvcLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = MsvcLinker()
        vars = link.default_vars()
        assert vars["cmd"] == "link.exe"
        assert vars["flags"] == ["/nologo"]
        assert vars["libs"] == []
        assert vars["libdirs"] == []
        assert "progcmd" in vars
        assert "sharedcmd" in vars
        # progcmd and sharedcmd are now list templates
        progcmd = vars["progcmd"]
        sharedcmd = vars["sharedcmd"]
        assert isinstance(progcmd, list)
        assert isinstance(sharedcmd, list)
        # Output uses $$out which becomes $out for ninja
        assert "/OUT:$$out" in progcmd
        assert "/DLL" in sharedcmd

    def test_builders(self):
        link = MsvcLinker()
        builders = link.builders()
        assert "Program" in builders
        assert "SharedLibrary" in builders
        prog_builder = builders["Program"]
        assert ".exe" in prog_builder.target_suffixes
        shared_builder = builders["SharedLibrary"]
        assert ".dll" in shared_builder.target_suffixes

    def test_shared_library_is_multi_output_builder(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]
        assert isinstance(shared_builder, MultiOutputBuilder)

    def test_shared_library_outputs(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]

        # Check output specs
        outputs = shared_builder.outputs
        assert len(outputs) == 3

        # Primary should be .dll
        assert outputs[0].name == "primary"
        assert outputs[0].suffix == ".dll"
        assert outputs[0].implicit is False

        # Import lib should be .lib
        assert outputs[1].name == "import_lib"
        assert outputs[1].suffix == ".lib"
        assert outputs[1].implicit is False

        # Export file should be .exp and implicit
        assert outputs[2].name == "export_file"
        assert outputs[2].suffix == ".exp"
        assert outputs[2].implicit is True

    def test_shared_library_returns_output_group(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.flags = ["/nologo"]
        env.link.sharedcmd = ["$link.cmd", "/DLL", "/OUT:$$out", "$$in"]

        result = shared_builder(env, "build/mylib.dll", ["a.obj", "b.obj"])

        assert isinstance(result, OutputGroup)
        assert result.primary.path == Path("build/mylib.dll")
        assert result.import_lib.path == Path("build/mylib.lib")
        assert result.export_file.path == Path("build/mylib.exp")

    def test_sharedcmd_includes_implib(self):
        link = MsvcLinker()
        vars = link.default_vars()
        sharedcmd = vars["sharedcmd"]
        # Check that IMPLIB flag is in the command
        assert any("/IMPLIB:" in str(item) for item in sharedcmd)


class TestMsvcToolchain:
    def test_creation(self):
        tc = MsvcToolchain()
        assert tc.name == "msvc"

    def test_tools_empty_before_configure(self):
        tc = MsvcToolchain()
        # Tools should be empty before configure
        assert tc.tools == {}

    def test_configure_returns_false_on_non_windows(self):
        platform = get_platform()
        if not platform.is_windows:
            tc = MsvcToolchain()

            # Create a mock config object
            class MockConfig:
                pass

            # Should return False on non-Windows
            result = tc._configure_tools(MockConfig())
            assert result is False
