# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.msvc."""

import pytest

from pcons.configure.platform import get_platform
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
        assert "$cc.cmd" in vars["objcmd"]
        assert "/c" in vars["objcmd"]
        assert "/Fo$out" in vars["objcmd"]

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
        assert "/OUT:$out" in vars["libcmd"]

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
        assert "/OUT:$out" in vars["progcmd"]
        assert "/DLL" in vars["sharedcmd"]

    def test_builders(self):
        link = MsvcLinker()
        builders = link.builders()
        assert "Program" in builders
        assert "SharedLibrary" in builders
        prog_builder = builders["Program"]
        assert ".exe" in prog_builder.target_suffixes
        shared_builder = builders["SharedLibrary"]
        assert ".dll" in shared_builder.target_suffixes


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
