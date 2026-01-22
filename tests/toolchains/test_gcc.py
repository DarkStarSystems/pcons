# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.gcc."""

from pcons.configure.platform import Platform
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
