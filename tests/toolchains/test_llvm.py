# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.llvm."""

import pytest

from pcons.configure.platform import Platform, get_platform
from pcons.toolchains.llvm import (
    ClangCCompiler,
    ClangCxxCompiler,
    LlvmArchiver,
    LlvmLinker,
    LlvmToolchain,
)


class TestClangCCompiler:
    def test_creation(self):
        cc = ClangCCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_default_vars(self):
        cc = ClangCCompiler()
        vars = cc.default_vars()
        assert vars["cmd"] == "clang"
        assert vars["flags"] == []
        assert vars["includes"] == []
        assert vars["defines"] == []
        assert "objcmd" in vars
        assert "$cc.cmd" in vars["objcmd"]

    def test_builders(self):
        cc = ClangCCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert obj_builder.name == "Object"
        assert ".c" in obj_builder.src_suffixes


class TestClangCxxCompiler:
    def test_creation(self):
        cxx = ClangCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = ClangCxxCompiler()
        vars = cxx.default_vars()
        assert vars["cmd"] == "clang++"
        assert "objcmd" in vars
        assert "$cxx.cmd" in vars["objcmd"]

    def test_builders(self):
        cxx = ClangCxxCompiler()
        builders = cxx.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert ".cpp" in obj_builder.src_suffixes
        assert ".cxx" in obj_builder.src_suffixes
        assert ".cc" in obj_builder.src_suffixes


class TestLlvmArchiver:
    def test_creation(self):
        ar = LlvmArchiver()
        assert ar.name == "ar"

    def test_default_vars(self):
        ar = LlvmArchiver()
        vars = ar.default_vars()
        assert vars["cmd"] == "llvm-ar"
        assert vars["flags"] == "rcs"
        assert "libcmd" in vars

    def test_builders(self):
        ar = LlvmArchiver()
        builders = ar.builders()
        assert "StaticLibrary" in builders
        lib_builder = builders["StaticLibrary"]
        assert lib_builder.name == "StaticLibrary"


class TestLlvmLinker:
    def test_creation(self):
        link = LlvmLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = LlvmLinker()
        vars = link.default_vars()
        assert vars["cmd"] == "clang"
        assert vars["flags"] == []
        assert vars["libs"] == []
        assert vars["libdirs"] == []
        assert "progcmd" in vars
        assert "sharedcmd" in vars

    def test_shared_flag_platform_specific(self):
        link = LlvmLinker()
        vars = link.default_vars()
        platform = get_platform()
        if platform.is_macos:
            assert "-dynamiclib" in vars["sharedcmd"]
        else:
            assert "-shared" in vars["sharedcmd"]

    def test_builders(self):
        link = LlvmLinker()
        builders = link.builders()
        assert "Program" in builders
        assert "SharedLibrary" in builders


class TestLlvmToolchain:
    def test_creation(self):
        tc = LlvmToolchain()
        assert tc.name == "llvm"

    def test_tools_empty_before_configure(self):
        tc = LlvmToolchain()
        # Tools should be empty before configure
        assert tc.tools == {}
