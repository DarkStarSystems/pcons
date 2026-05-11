# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.llvm."""

import re
from pathlib import Path

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
        assert handler.object_suffix == get_platform().object_suffix
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
        assert handler.object_suffix == get_platform().object_suffix
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
        assert handler.object_suffix == get_platform().object_suffix
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

    @pytest.mark.parametrize("suffix", [".cppm", ".ixx", ".cxxm", ".c++m"])
    def test_source_handler_module_interface(self, suffix):
        """All recognized C++20 module-interface extensions map to cxx_module."""
        tc = LlvmToolchain()
        handler = tc.get_source_handler(suffix)
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx_module"


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


class TestLibcxxModulesManifest:
    """Tests for libc++ std-module manifest discovery and parsing.

    We don't run a real clang here — we monkeypatch subprocess.run to
    return canned output so the tests work even on machines without
    a libc++.modules.json available (e.g. Apple Clang).
    """

    def test_finds_manifest_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from pcons.toolchains.llvm import _find_libcxx_modules_manifest

        manifest = tmp_path / "libc++.modules.json"
        manifest.write_text("{}", encoding="utf-8")

        class _Result:
            returncode = 0
            stdout = f"{manifest}\n"
            stderr = ""

        captured: dict[str, list[str]] = {}

        def _fake_run(cmd: list[str], **_kwargs: object) -> _Result:
            captured["cmd"] = cmd
            return _Result()

        monkeypatch.setattr("pcons.toolchains.llvm.subprocess.run", _fake_run)
        found = _find_libcxx_modules_manifest("clang++", ["-std=c++23"])
        assert found == manifest
        # Must query with -stdlib=libc++ since user didn't pass one.
        assert "-stdlib=libc++" in captured["cmd"]
        assert any(
            re.search(r"-print-file-name=(c++/)?libc\+\+.modules.json", cmd)
            for cmd in captured["cmd"]
        )

    def test_returns_none_when_compiler_echoes_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Clang's -print-file-name echoes the query unchanged when it
        # doesn't find the file (Apple Clang's behavior for the std
        # module manifest). That must surface as "no manifest".
        from pcons.toolchains.llvm import _find_libcxx_modules_manifest

        class _Result:
            returncode = 0
            stdout = "c++/libc++.modules.json\n"
            stderr = ""

        monkeypatch.setattr(
            "pcons.toolchains.llvm.subprocess.run",
            lambda *_a, **_k: _Result(),
        )
        assert _find_libcxx_modules_manifest("clang++", []) is None

    def test_returns_none_when_compiler_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.toolchains.llvm import _find_libcxx_modules_manifest

        def _enoent(*_a: object, **_k: object) -> None:
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr("pcons.toolchains.llvm.subprocess.run", _enoent)
        assert _find_libcxx_modules_manifest("clang++", []) is None

    def test_passes_through_user_stdlib(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # If the user is on libstdc++ (or any non-libc++ stdlib), we ask
        # the compiler about *their* stdlib, not libc++.
        from pcons.toolchains.llvm import _find_libcxx_modules_manifest

        manifest = tmp_path / "modules.json"
        manifest.write_text("{}", encoding="utf-8")

        class _Result:
            returncode = 0
            stdout = f"{manifest}\n"
            stderr = ""

        captured: dict[str, list[str]] = {}

        def _fake_run(cmd: list[str], **_kw: object) -> _Result:
            captured["cmd"] = cmd
            return _Result()

        monkeypatch.setattr("pcons.toolchains.llvm.subprocess.run", _fake_run)
        _find_libcxx_modules_manifest("clang++", ["-stdlib=libstdc++"])
        assert "-stdlib=libstdc++" in captured["cmd"]
        assert "-stdlib=libc++" not in captured["cmd"]

    def test_parse_resolves_relative_paths(self, tmp_path: Path) -> None:
        # The manifest stores paths relative to its own directory; the
        # parser must resolve them to absolute paths.
        from pcons.toolchains.llvm import _parse_libcxx_manifest

        share = tmp_path / "share" / "libc++" / "v1"
        share.mkdir(parents=True)
        (share / "std.cppm").write_text("// std module\n", encoding="utf-8")
        (share / "std.compat.cppm").write_text("// std.compat\n", encoding="utf-8")

        manifest_dir = tmp_path / "lib" / "c++"
        manifest_dir.mkdir(parents=True)
        manifest = manifest_dir / "libc++.modules.json"
        import json as _json

        manifest.write_text(
            _json.dumps(
                {
                    "version": 1,
                    "modules": [
                        {
                            "logical-name": "std",
                            "source-path": "../../share/libc++/v1/std.cppm",
                            "local-arguments": {
                                "system-include-directories": ["../../share/libc++/v1"]
                            },
                        },
                        {
                            "logical-name": "std.compat",
                            "source-path": "../../share/libc++/v1/std.compat.cppm",
                            "local-arguments": {
                                "system-include-directories": ["../../share/libc++/v1"]
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        modules = _parse_libcxx_manifest(manifest)
        assert set(modules.keys()) == {"std", "std.compat"}
        assert modules["std"]["source-path"] == (share / "std.cppm").resolve()
        assert modules["std"]["system-include-directories"] == [share.resolve()]

    def test_parse_skips_malformed_entries(self, tmp_path: Path) -> None:
        import json as _json

        from pcons.toolchains.llvm import _parse_libcxx_manifest

        manifest = tmp_path / "libc++.modules.json"
        manifest.write_text(
            _json.dumps(
                {
                    "modules": [
                        {"logical-name": "std"},  # missing source-path
                        {"source-path": "x.cppm"},  # missing logical-name
                        {
                            "logical-name": "std",
                            "source-path": "x.cppm",
                        },  # ok, no local-arguments
                    ]
                }
            ),
            encoding="utf-8",
        )
        modules = _parse_libcxx_manifest(manifest)
        assert list(modules.keys()) == ["std"]
        assert modules["std"]["system-include-directories"] == []

    def test_parse_rejects_unknown_version(self, tmp_path: Path) -> None:
        # libc++ reserved `version` for breaking format changes. If the
        # manifest declares a version we don't know, refuse rather than
        # silently misparse — a future format could put `source-path`
        # somewhere else, and we'd happily build the wrong file.
        import json as _json

        from pcons.toolchains.llvm import _parse_libcxx_manifest

        manifest = tmp_path / "libc++.modules.json"
        manifest.write_text(
            _json.dumps({"version": 2, "modules": []}), encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="version"):
            _parse_libcxx_manifest(manifest)

    def test_parse_accepts_missing_version_field(self, tmp_path: Path) -> None:
        # A manifest with no `version` field is treated as legacy — accept
        # it. (Some early LLVM packages shipped this shape.)
        import json as _json

        from pcons.toolchains.llvm import _parse_libcxx_manifest

        manifest = tmp_path / "libc++.modules.json"
        manifest.write_text(
            _json.dumps(
                {"modules": [{"logical-name": "std", "source-path": "x.cppm"}]}
            ),
            encoding="utf-8",
        )
        modules = _parse_libcxx_manifest(manifest)
        assert list(modules.keys()) == ["std"]
