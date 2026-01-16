# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.gcc."""

import pytest

from pcons.configure.platform import Platform, get_platform
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
