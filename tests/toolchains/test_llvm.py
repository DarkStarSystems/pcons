# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.llvm."""

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
        # cmd is llvm-ar if available, otherwise falls back to ar
        assert vars["cmd"] in ("llvm-ar", "ar")
        # flags is now a list (for consistency with subst)
        assert vars["flags"] == ["rcs"]
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


class TestLlvmSourceHandlers:
    """Tests for LLVM source handler methods."""

    def test_source_handler_c(self):
        """Test that .c files are handled correctly."""
        from pcons.core.subst import TargetPath

        tc = LlvmToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "c"
        assert handler.object_suffix == ".o"
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_source_handler_cpp(self):
        """Test that .cpp files are handled correctly."""
        tc = LlvmToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx"

    def test_source_handler_s_lowercase(self):
        """Test that .s (lowercase) files are handled as preprocessed assembly."""
        tc = LlvmToolchain()
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

        tc = LlvmToolchain()
        handler = tc.get_source_handler(".S")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "asm-cpp"
        assert handler.object_suffix == ".o"
        # Assembly needing preprocessing has gcc-style dependency tracking
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_source_handler_metal_on_macos(self, monkeypatch):
        """Test that .metal files are handled on macOS."""
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
        # Need to mock in both locations: unix.py (base class) and llvm.py
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: macos_platform
        )
        monkeypatch.setattr(
            "pcons.toolchains.llvm.get_platform", lambda: macos_platform
        )

        tc = LlvmToolchain()
        handler = tc.get_source_handler(".metal")
        assert handler is not None
        assert handler.tool_name == "metal"
        assert handler.language == "metal"
        assert handler.object_suffix == ".air"
        assert handler.command_var == "metalcmd"
        # Metal has no dependency tracking
        assert handler.depfile is None
        assert handler.deps_style is None

    def test_source_handler_metal_not_on_linux(self, monkeypatch):
        """Test that .metal files are not handled on Linux."""
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
        # Need to mock in both locations: unix.py (base class) and llvm.py
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )
        monkeypatch.setattr(
            "pcons.toolchains.llvm.get_platform", lambda: linux_platform
        )

        tc = LlvmToolchain()
        handler = tc.get_source_handler(".metal")
        # Metal is not supported on Linux
        assert handler is None

    def test_source_handler_objc(self):
        """Test that .m files are handled as Objective-C."""
        tc = LlvmToolchain()
        handler = tc.get_source_handler(".m")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "objc"

    def test_source_handler_unknown(self):
        """Test that unknown suffixes return None."""
        tc = LlvmToolchain()
        handler = tc.get_source_handler(".xyz")
        assert handler is None


class TestMetalCompiler:
    """Tests for the Metal compiler tool."""

    def test_creation(self):
        from pcons.toolchains.llvm import MetalCompiler

        metal = MetalCompiler()
        assert metal.name == "metal"
        assert metal.language == "metal"

    def test_default_vars(self):
        from pcons.toolchains.llvm import MetalCompiler

        metal = MetalCompiler()
        vars = metal.default_vars()
        assert vars["cmd"] == "xcrun"
        assert "metalcmd" in vars
        metalcmd = vars["metalcmd"]
        assert "metal" in metalcmd
        assert "-c" in metalcmd

    def test_builders(self):
        from pcons.toolchains.llvm import MetalCompiler

        metal = MetalCompiler()
        builders = metal.builders()
        assert "MetalObject" in builders
        builder = builders["MetalObject"]
        assert builder.name == "MetalObject"
        assert ".metal" in builder.src_suffixes
        assert ".air" in builder.target_suffixes


class TestLlvmCompileFlagsForTargetType:
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

        tc = LlvmToolchain()
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

        tc = LlvmToolchain()
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

        tc = LlvmToolchain()
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

        tc = LlvmToolchain()
        flags = tc.get_compile_flags_for_target_type("program")
        assert "-fPIC" not in flags
        assert flags == []
