# SPDX-License-Identifier: MIT
"""Tests for ConanFinder."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.finders.conan import ConanFinder


class TestConanFinderBasic:
    """Basic tests for ConanFinder."""

    def test_name(self) -> None:
        """Test finder name."""
        finder = ConanFinder()
        assert finder.name == "conan"

    def test_is_available_without_conan(self) -> None:
        """Test is_available when conan is not installed."""
        finder = ConanFinder(conan_cmd="nonexistent_conan_xyz")
        assert finder.is_available() is False

    def test_profile_path(self, tmp_path: Path) -> None:
        """Test profile path calculation."""
        finder = ConanFinder(output_folder=tmp_path / "deps")
        assert finder.profile_path == tmp_path / "deps" / "pcons-profile"

    def test_pkgconfig_dir(self, tmp_path: Path) -> None:
        """Test pkgconfig directory path."""
        finder = ConanFinder(output_folder=tmp_path / "deps")
        assert finder.pkgconfig_dir == tmp_path / "deps"

    def test_repr(self) -> None:
        """Test string representation."""
        finder = ConanFinder(
            conanfile="test.txt",
            output_folder="build/deps",
        )
        repr_str = repr(finder)
        assert "ConanFinder" in repr_str
        assert "test.txt" in repr_str
        # Check for path components (Windows uses backslash)
        assert "build" in repr_str and "deps" in repr_str


class TestConanFinderProfile:
    """Tests for profile generation."""

    def test_sync_profile_creates_file(self, tmp_path: Path) -> None:
        """Test that sync_profile creates profile file."""
        finder = ConanFinder(output_folder=tmp_path)
        profile_path = finder.sync_profile()

        assert profile_path.exists()
        content = profile_path.read_text()
        assert "[settings]" in content

    def test_sync_profile_includes_os(self, tmp_path: Path) -> None:
        """Test that profile includes OS setting."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile()

        content = finder.profile_path.read_text()
        # Should have one of these OS values
        assert any(
            os_name in content for os_name in ["os=Macos", "os=Linux", "os=Windows"]
        )

    def test_sync_profile_includes_build_type(self, tmp_path: Path) -> None:
        """Test that profile includes build type."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile(build_type="Debug")

        content = finder.profile_path.read_text()
        assert "build_type=Debug" in content

    def test_set_profile_setting(self, tmp_path: Path) -> None:
        """Test custom profile settings."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.set_profile_setting("compiler.cppstd", "20")
        finder.sync_profile()

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=20" in content

    def test_set_profile_conf(self, tmp_path: Path) -> None:
        """Test custom profile conf values."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.set_profile_conf("tools.build:cxxflags", ["-Wall", "-Werror"])
        finder.sync_profile()

        content = finder.profile_path.read_text()
        assert "[conf]" in content
        assert "tools.build:cxxflags" in content

    def test_sync_profile_cppstd_explicit(self, tmp_path: Path) -> None:
        """Test explicit cppstd parameter."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile(cppstd="20")

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=20" in content

    def test_sync_profile_cppstd_inferred_from_env(self, tmp_path: Path) -> None:
        """Test cppstd inferred from env.cxx.flags."""
        from types import SimpleNamespace

        env = SimpleNamespace(cxx=SimpleNamespace(flags=["-std=c++23"]))
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile(env=env)

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=23" in content

    def test_sync_profile_cppstd_explicit_overrides_env(self, tmp_path: Path) -> None:
        """Explicit cppstd takes precedence over env inference."""
        from types import SimpleNamespace

        env = SimpleNamespace(cxx=SimpleNamespace(flags=["-std=c++17"]))
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile(env=env, cppstd="20")

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=20" in content
        assert "compiler.cppstd=17" not in content

    def test_sync_profile_cppstd_set_profile_setting_overrides(
        self, tmp_path: Path
    ) -> None:
        """set_profile_setting overrides both explicit and inferred cppstd."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.set_profile_setting("compiler.cppstd", "14")
        finder.sync_profile(cppstd="20")

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=14" in content

    def test_infer_cppstd_variants(self) -> None:
        """Test cppstd inference from various flag formats."""
        from types import SimpleNamespace

        cases = {
            "-std=c++17": "17",
            "-std=c++20": "20",
            "-std=c++23": "23",
            "-std=gnu++20": "20",
            "-std=c++2a": "20",
            "-std=c++2b": "23",
            "/std:c++17": "17",
            "/std:c++latest": "latest",
        }
        for flag, expected in cases.items():
            env = SimpleNamespace(cxx=SimpleNamespace(flags=[flag]))
            result = ConanFinder._infer_cppstd(env)
            assert result == expected, f"Flag {flag}: expected {expected}, got {result}"

    def test_infer_cppstd_no_env(self) -> None:
        """Test that None env returns None."""
        assert ConanFinder._infer_cppstd(None) is None

    def test_infer_cppstd_no_std_flag(self) -> None:
        """Test that env without -std flag returns None."""
        from types import SimpleNamespace

        env = SimpleNamespace(cxx=SimpleNamespace(flags=["-Wall", "-O2"]))
        assert ConanFinder._infer_cppstd(env) is None


class TestConanFinderInstall:
    """Tests for conan install with mocked subprocess."""

    def test_install_without_conan_raises(self, tmp_path: Path) -> None:
        """Test that install raises when conan is not available."""
        finder = ConanFinder(
            conanfile=tmp_path / "conanfile.txt",
            output_folder=tmp_path / "deps",
            conan_cmd="nonexistent_conan_xyz",
        )

        with pytest.raises(RuntimeError, match="Conan is not available"):
            finder.install()

    def test_install_runs_conan_command(self, tmp_path: Path) -> None:
        """Test that install runs the correct conan command."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
            build_missing=True,
        )
        finder.sync_profile()

        # Create a mock .pc file so parsing succeeds
        pc_file = output_folder / "zlib.pc"
        pc_file.write_text(
            """prefix=/usr/local
libdir=${prefix}/lib
includedir=${prefix}/include

Name: zlib
Description: zlib compression library
Version: 1.3
Libs: -L${libdir} -lz
Cflags: -I${includedir}
"""
        )

        captured_calls: list = []

        def mock_run(cmd, **kwargs):
            captured_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Installing packages..."
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            finder.install()

            # Find the conan install call (not pkg-config calls)
            # Note: conan may be invoked via "uvx conan" so check entire command
            conan_calls = [c for c in captured_calls if "conan" in str(c)]
            assert len(conan_calls) > 0, f"Expected conan call, got: {captured_calls}"
            call_args = conan_calls[0]
            assert "install" in call_args
            assert "-g" in call_args
            assert "PkgConfigDeps" in call_args
            assert "--build=missing" in call_args

    def test_install_passes_custom_conanfile_path_not_parent_dir(
        self, tmp_path: Path
    ) -> None:
        """A custom conanfile name must reach `conan install` as the file
        itself, not its containing directory — otherwise Conan silently
        falls back to resolving the default conanfile.txt in that dir.
        """
        conanfile = tmp_path / "conanfile-ci.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")
        # A default conanfile.txt also exists alongside it, with different
        # requirements, to prove the wrong (default) one isn't picked up.
        (tmp_path / "conanfile.txt").write_text("[requires]\nopenssl/3.0\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        pc_file = output_folder / "zlib.pc"
        pc_file.write_text(
            "prefix=/usr/local\nName: zlib\nVersion: 1.3\nLibs:\nCflags:\n"
        )

        captured_calls: list = []

        def mock_run(cmd, **kwargs):
            captured_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            finder.install()

        conan_calls = [c for c in captured_calls if "install" in c]
        assert len(conan_calls) == 1
        call_args = conan_calls[0]
        assert str(conanfile) in call_args
        # The bare parent directory must not be passed instead of the file —
        # that would make conan silently resolve the default conanfile.txt.
        assert str(tmp_path) not in call_args

    def test_install_parses_pc_files(self, tmp_path: Path) -> None:
        """Test that install parses generated .pc files."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        # Create mock .pc file
        pc_file = output_folder / "zlib.pc"
        pc_file.write_text(
            """prefix=/opt/conan
libdir=${prefix}/lib
includedir=${prefix}/include

Name: zlib
Description: zlib compression library
Version: 1.3.1
Libs: -L${libdir} -lz
Cflags: -I${includedir}
"""
        )

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            packages = finder.install()

            assert "zlib" in packages
            pkg = packages["zlib"]
            assert pkg.name == "zlib"
            assert pkg.found_by == "conan"

    def test_install_raises_when_nothing_parses(self, tmp_path: Path) -> None:
        """Install must fail when conan succeeds but no .pc files are found."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
            pytest.raises(RuntimeError, match=re.escape(str(output_folder))),
        ):
            finder.install()

    def test_install_reinstalls_when_cache_valid_but_pc_files_missing(
        self, tmp_path: Path
    ) -> None:
        """A valid cache key with no .pc files (e.g. the output folder was
        cleaned but the cache key file survived) must not be treated as a
        cache hit returning {} — it must fall through to a real install.
        """
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()
        finder._save_cache_key()
        assert finder._is_cache_valid() is True

        def mock_run(cmd, **kwargs):
            if "install" in cmd:
                (output_folder / "zlib.pc").write_text(
                    "prefix=/usr/local\nName: zlib\nVersion: 1.3\nLibs:\nCflags:\n"
                )
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            packages = finder.install()

        assert "zlib" in packages


class TestConanFinderCaching:
    """Tests for caching functionality."""

    def test_cache_key_changes_with_conanfile(self, tmp_path: Path) -> None:
        """Test that cache key changes when conanfile changes."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        key1 = finder._compute_cache_key()

        # Modify conanfile
        conanfile.write_text("[requires]\nzlib/1.3.1\n")
        key2 = finder._compute_cache_key()

        assert key1 != key2

    def test_cache_key_changes_with_profile(self, tmp_path: Path) -> None:
        """Test that cache key changes when profile changes."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile(build_type="Release")
        key1 = finder._compute_cache_key()

        finder.sync_profile(build_type="Debug")
        key2 = finder._compute_cache_key()

        assert key1 != key2

    def test_is_cache_valid_false_initially(self, tmp_path: Path) -> None:
        """Test that cache is invalid initially."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile()

        assert finder._is_cache_valid() is False

    def test_cache_valid_after_save(self, tmp_path: Path) -> None:
        """Test that cache is valid after saving."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=tmp_path,
        )
        finder.sync_profile()
        finder._save_cache_key()

        assert finder._is_cache_valid() is True


class TestConanFinderPcParsing:
    """Tests for .pc file parsing."""

    def test_parse_single_pc_file(self, tmp_path: Path) -> None:
        """Test parsing a single .pc file."""
        pc_file = tmp_path / "test.pc"
        pc_file.write_text(
            """prefix=/usr/local
libdir=${prefix}/lib
includedir=${prefix}/include

Name: test
Version: 1.2.3
Cflags: -I${includedir} -DTEST_DEFINE
Libs: -L${libdir} -ltest -lpthread
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        pkg = finder._parse_single_pc_file(pc_file)

        assert pkg is not None
        assert pkg.name == "test"
        assert pkg.version == "1.2.3"
        assert pkg.prefix == "/usr/local"
        assert "/usr/local/include" in pkg.include_dirs
        assert "TEST_DEFINE" in pkg.defines
        assert "/usr/local/lib" in pkg.library_dirs
        assert "test" in pkg.libraries
        assert "pthread" in pkg.libraries

    def test_parse_single_pc_file_quoted_paths(self, tmp_path: Path) -> None:
        """Conan's PkgConfigDeps quotes path values, quotes must be stripped."""
        pc_file = tmp_path / "nanobind.pc"
        pc_file.write_text(
            """prefix=C:/Users/me/.conan2/p/nanob123/p
libdir=${prefix}/lib
includedir=${prefix}/nanobind/include

Name: nanobind
Version: 2.12.0
Libs: -L"${libdir}"
Cflags: -I"${includedir}"
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        pkg = finder._parse_single_pc_file(pc_file)

        assert pkg is not None
        assert pkg.prefix == "C:/Users/me/.conan2/p/nanob123/p"
        assert pkg.include_dirs == ["C:/Users/me/.conan2/p/nanob123/p/nanobind/include"]
        assert pkg.library_dirs == ["C:/Users/me/.conan2/p/nanob123/p/lib"]

    def test_parse_pc_files_manually(self, tmp_path: Path) -> None:
        """Test manual .pc file parsing."""
        # Create multiple .pc files
        (tmp_path / "foo.pc").write_text(
            """Name: foo
Version: 1.0
Libs: -lfoo
"""
        )
        (tmp_path / "bar.pc").write_text(
            """Name: bar
Version: 2.0
Libs: -lbar
Cflags: -I/opt/bar/include
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        packages = finder._parse_pc_files_manually()

        assert "foo" in packages
        assert "bar" in packages
        assert packages["foo"].version == "1.0"
        assert packages["bar"].version == "2.0"
        assert "bar" in packages["bar"].libraries
        assert "/opt/bar/include" in packages["bar"].include_dirs

    @pytest.mark.parametrize(
        "gen_relpath",
        [
            # single-config (Make/Ninja, e.g. gcc): build/<build_type>/generators
            "build/Release/generators",
            # multi-config (MSVC/Xcode): build/generators — the Windows layout
            "build/generators",
        ],
    )
    def test_parse_discovers_pc_files_across_cmake_layouts(
        self, tmp_path: Path, gen_relpath: str
    ) -> None:
        """.pc files must be found in whichever generators/ subfolder they land.

        Regression: cmake_layout puts the generated .pc files in
        build/<build_type>/generators (single-config) or build/generators
        (multi-config, i.e. MSVC on Windows). Discovery previously only looked
        at the former, so on Windows nothing was found and the package dict came
        back empty (KeyError 'nanobind' on use).
        """
        gen_dir = tmp_path / gen_relpath
        gen_dir.mkdir(parents=True)
        (gen_dir / "nanobind.pc").write_text(
            """Name: nanobind
Version: 2.12.0
Cflags: -I/opt/nanobind/include
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        packages = finder._parse_pkgconfig_files()

        assert "nanobind" in packages
        assert packages["nanobind"].version == "2.12.0"
        assert "/opt/nanobind/include" in packages["nanobind"].include_dirs

    def test_parse_is_independent_of_system_pkgconfig(self, tmp_path: Path) -> None:
        """Conan .pc files are parsed deterministically, not via pkg-config.

        Regression: GitHub's windows-latest runners carry Strawberry Perl's old
        pkg-config on PATH, and it failed to resolve nanobind's transitive
        ``Requires`` — silently dropping the package (KeyError 'nanobind'
        downstream). The same build worked on machines with no pkg-config. The
        finder must parse its own generated files itself so the result is the
        same everywhere, including merging transitive Requires and stripping the
        quotes Conan puts around paths.
        """
        gen = tmp_path / "build" / "generators"
        gen.mkdir(parents=True)
        (gen / "nanobind.pc").write_text(
            "prefix=/p/nanobind\n"
            "includedir=${prefix}/include\n"
            "Name: nanobind\n"
            "Version: 2.12.0\n"
            'Cflags: -I"${includedir}"\n'
            "Requires: tsl-robin-map\n"
        )
        (gen / "tsl-robin-map.pc").write_text(
            "prefix=/p/robin\n"
            "includedir=${prefix}/include\n"
            "Name: tsl-robin-map\n"
            "Version: 1.4.0\n"
            'Cflags: -I"${includedir}"\n'
        )

        finder = ConanFinder(output_folder=tmp_path)
        packages = finder._parse_pkgconfig_files()

        assert "nanobind" in packages
        nb = packages["nanobind"]
        # Own include dir, with Conan's surrounding quotes stripped.
        assert "/p/nanobind/include" in nb.include_dirs
        # Transitive include from tsl-robin-map merged in (what pkg-config
        # --cflags would have done).
        assert "/p/robin/include" in nb.include_dirs

    def test_transitive_merge_preserves_repeated_framework_flags(
        self, tmp_path: Path
    ) -> None:
        """macOS ``-framework`` flags survive the transitive Requires merge.

        Regression: _merge_transitive_requires deduped link_flags token by
        token, so the repeated literal ``-framework`` in
        ``Libs: -framework Foundation -framework IOKit -framework CoreGraphics``
        collapsed to ``-framework Foundation IOKit CoreGraphics`` — IOKit and
        CoreGraphics then look like input files and the link fails. The merge
        only runs when the package has a transitive ``Require`` (OpenColorIO →
        expat here), which is why a deps-free .pc never tripped it.
        """
        gen = tmp_path / "build" / "generators"
        gen.mkdir(parents=True)
        (gen / "OpenColorIO.pc").write_text(
            "Name: OpenColorIO\n"
            "Version: 2.3.0\n"
            "Libs: -lOpenColorIO -framework Foundation -framework IOKit "
            "-framework CoreGraphics\n"
            "Requires: expat\n"
        )
        (gen / "expat.pc").write_text("Name: expat\nVersion: 2.6.0\nLibs: -lexpat\n")

        finder = ConanFinder(output_folder=tmp_path)
        packages = finder._parse_pkgconfig_files()

        flags = packages["OpenColorIO"].link_flags
        # Each framework keeps its preceding -framework, and none collapsed.
        assert flags.count("-framework") == 3
        for fw in ("Foundation", "IOKit", "CoreGraphics"):
            assert flags[flags.index(fw) - 1] == "-framework"

    def test_transitive_merge_keeps_chained_xlinker_directives(
        self, tmp_path: Path
    ) -> None:
        """-Xlinker pass-through survives the merge without corruption.

        ``-Xlinker -rpath -Xlinker /a -Xlinker -rpath -Xlinker /b`` forwards
        ``-rpath /a -rpath /b`` to the linker. Pair-deduping the repeated
        ``-Xlinker -rpath`` would drop the second pair and orphan ``/b``, so
        -X-family flags are kept verbatim rather than deduped.
        """
        gen = tmp_path / "build" / "generators"
        gen.mkdir(parents=True)
        (gen / "OpenColorIO.pc").write_text(
            "Name: OpenColorIO\n"
            "Version: 2.3.0\n"
            "Libs: -lOpenColorIO -Xlinker -rpath -Xlinker /a "
            "-Xlinker -rpath -Xlinker /b\n"
            "Requires: expat\n"
        )
        (gen / "expat.pc").write_text("Name: expat\nVersion: 2.6.0\nLibs: -lexpat\n")

        finder = ConanFinder(output_folder=tmp_path)
        flags = finder._parse_pkgconfig_files()["OpenColorIO"].link_flags
        assert flags.count("-Xlinker") == 4
        assert flags.count("-rpath") == 2
        assert "/a" in flags and "/b" in flags


class TestConanFinderFind:
    """Tests for find() method."""

    def test_find_without_install_returns_none(self, tmp_path: Path) -> None:
        """Test find returns None when install hasn't been called."""
        finder = ConanFinder(output_folder=tmp_path)
        result = finder.find("zlib")
        assert result is None

    def test_find_after_parsing(self, tmp_path: Path) -> None:
        """Test find after .pc files are parsed."""
        # Create .pc file with a unique package name to avoid system packages
        (tmp_path / "pcons_test_pkg.pc").write_text(
            """Name: pcons_test_pkg
Version: 2.5.0
Libs: -lpcons_test
Cflags: -I/opt/pcons_test/include
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        # Use manual parsing to avoid pkg-config finding system packages
        finder._packages = finder._parse_pc_files_manually()

        result = finder.find("pcons_test_pkg")
        assert result is not None
        assert result.name == "pcons_test_pkg"
        assert result.version == "2.5.0"

    def test_find_nonexistent_package(self, tmp_path: Path) -> None:
        """Test find with nonexistent package."""
        (tmp_path / "foo.pc").write_text("Name: foo\nVersion: 1.0\n")

        finder = ConanFinder(output_folder=tmp_path)
        finder._parse_pkgconfig_files()

        result = finder.find("nonexistent")
        assert result is None


class _FakeCompilerTool:
    """Stand-in for a Tool: only ``default_vars()['cmd']`` is read by
    ``ConanFinder._get_toolchain_compiler_cmd``.
    """

    def __init__(self, cmd: str) -> None:
        self._cmd = cmd

    def default_vars(self) -> dict[str, str]:
        return {"cmd": self._cmd}


class _FakeToolchain:
    """Stand-in for a Toolchain exposing only ``.name`` and ``.tools`` —
    the attributes ConanFinder's compiler detection reads. Real toolchains
    never expose a ``.version`` attribute, so tests must go through the
    same cc/cxx command path the real code uses, not a shortcut.
    """

    def __init__(self, name: str, cc_cmd: str, cxx_cmd: str | None = None) -> None:
        self.name = name
        self.tools = {
            "cc": _FakeCompilerTool(cc_cmd),
            "cxx": _FakeCompilerTool(cxx_cmd or cc_cmd),
        }


class TestConanFinderWithToolchain:
    """Tests for toolchain integration."""

    def test_detect_compiler_settings_with_gcc_toolchain(self, tmp_path: Path) -> None:
        """Compiler version must be detected by running the toolchain's
        actual compiler command, not a bare "gcc" resolved from PATH.
        """
        finder = ConanFinder(output_folder=tmp_path)
        toolchain = _FakeToolchain("gcc", cc_cmd="/opt/gcc-13/bin/gcc-13")

        def mock_run(cmd, **kwargs):
            assert cmd[0] == "/opt/gcc-13/bin/gcc-13"
            result = MagicMock()
            result.returncode = 0
            result.stdout = "gcc (Ubuntu 13.2.0-4ubuntu3) 13.2.0\n"
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            settings = finder._detect_compiler_settings(toolchain)

        assert settings["compiler"] == "gcc"
        assert settings["compiler.version"] == "13"
        assert settings["compiler.libcxx"] == "libstdc++11"

    def test_detect_compiler_version_uses_toolchain_cmd_not_path(
        self, tmp_path: Path
    ) -> None:
        """A gcc-13 toolchain must not be reported using whatever "gcc"
        happens to resolve first on PATH (e.g. an unrelated gcc-11).
        """
        finder = ConanFinder(output_folder=tmp_path)
        toolchain = _FakeToolchain("gcc", cc_cmd="/opt/gcc-13/bin/gcc-13")

        def mock_run(cmd, **kwargs):
            if cmd[0] in ("gcc", "clang"):
                raise AssertionError(f"looked up bare {cmd[0]!r} on PATH")
            result = MagicMock()
            result.returncode = 0
            result.stdout = "gcc (Ubuntu 13.2.0-4ubuntu3) 13.2.0\n"
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            settings = finder._detect_compiler_settings(toolchain)

        assert settings["compiler.version"] == "13"

    def test_detect_compiler_version_no_toolchain_maps_apple_clang_to_binary(
        self, tmp_path: Path
    ) -> None:
        """With no toolchain (sync_profile() called bare), the Conan compiler
        name "apple-clang" must be mapped to the real "clang" binary — it is
        not itself an executable — so version detection still works on macOS.
        """
        finder = ConanFinder(output_folder=tmp_path)
        invoked: list[str] = []

        def mock_run(cmd, **kwargs):
            invoked.append(cmd[0])
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Apple clang version 15.0.0 (clang-1500.0.40.1)\n"
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            version = finder._detect_compiler_version("apple-clang", None)

        assert version == "15"
        assert invoked == ["clang"]  # not the non-existent "apple-clang"

    def test_detect_compiler_settings_with_clang_toolchain(
        self, tmp_path: Path
    ) -> None:
        """Test compiler detection with Clang toolchain."""
        from pcons.configure.platform import Platform

        finder = ConanFinder(output_folder=tmp_path)
        toolchain = _FakeToolchain("clang", cc_cmd="/usr/lib/llvm-15/bin/clang")

        # Create a mock platform that's not macOS
        mock_platform = MagicMock(spec=Platform)
        mock_platform.is_macos = False
        mock_platform.is_linux = True
        mock_platform.is_windows = False
        mock_platform.arch = "x86_64"

        def mock_run(cmd, **kwargs):
            assert cmd[0] == "/usr/lib/llvm-15/bin/clang"
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Ubuntu clang version 15.0.7\n"
            result.stderr = ""
            return result

        original_platform = finder._platform
        finder._platform = mock_platform
        try:
            with patch("subprocess.run", side_effect=mock_run):
                settings = finder._detect_compiler_settings(toolchain)
        finally:
            finder._platform = original_platform

        assert settings["compiler"] == "clang"
        assert settings["compiler.version"] == "15"

    def test_detect_compiler_settings_with_msvc_toolchain(self, tmp_path: Path) -> None:
        """MSVC settings must include compiler.version and compiler.runtime
        (both mandatory in Conan 2 msvc profiles) so `conan install`
        doesn't fail on Windows.
        """
        finder = ConanFinder(output_folder=tmp_path)
        toolchain = _FakeToolchain("msvc", cc_cmd="cl.exe")

        def mock_run(cmd, **kwargs):
            assert cmd == ["cl.exe"]
            result = MagicMock()
            result.returncode = 2
            result.stdout = ""
            result.stderr = (
                "Microsoft (R) C/C++ Optimizing Compiler Version "
                "19.38.33130 for x64\n"
                "Copyright (C) Microsoft Corporation.  All rights reserved.\n"
            )
            return result

        with patch("subprocess.run", side_effect=mock_run):
            settings = finder._detect_compiler_settings(toolchain, build_type="Debug")

        assert settings["compiler"] == "msvc"
        assert settings["compiler.version"] == "193"
        assert settings["compiler.runtime"] == "dynamic"
        assert settings["compiler.runtime_type"] == "Debug"

    @pytest.mark.parametrize(
        ("cl_version", "expected"),
        [
            ("17.0", "170"),
            ("18.0", "180"),
            ("19.0", "190"),
            ("19.16", "191"),
            ("19.29", "192"),
            ("19.38", "193"),
            ("19.40", "194"),
        ],
    )
    def test_msvc_conan_version_mapping(self, cl_version: str, expected: str) -> None:
        """cl.exe's raw version must map to Conan's msvc version buckets."""
        assert ConanFinder._msvc_conan_version(cl_version) == expected

    def test_sync_profile_with_toolchain(self, tmp_path: Path) -> None:
        """Test profile generation with toolchain."""
        finder = ConanFinder(output_folder=tmp_path)
        toolchain = _FakeToolchain("gcc", cc_cmd="gcc")

        finder.sync_profile(toolchain=toolchain)

        content = finder.profile_path.read_text()
        assert "compiler=gcc" in content


class TestConanFinderCommandResolution:
    """Tests for conan command resolution."""

    def test_explicit_conan_cmd(self) -> None:
        """Test that explicit conan_cmd is used."""
        finder = ConanFinder(conan_cmd="/custom/path/conan")
        assert finder.conan_cmd == ["/custom/path/conan"]

    def test_pcons_conan_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test PCONS_CONAN environment variable."""
        monkeypatch.setenv("PCONS_CONAN", "/env/conan")
        # Clear any cached resolution
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/env/conan"]

    def test_conan_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CONAN environment variable."""
        monkeypatch.delenv("PCONS_CONAN", raising=False)
        monkeypatch.setenv("CONAN", "/env/conan2")
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/env/conan2"]

    def test_pcons_conan_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test PCONS_CONAN takes precedence over CONAN."""
        monkeypatch.setenv("PCONS_CONAN", "/pcons/conan")
        monkeypatch.setenv("CONAN", "/other/conan")
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/pcons/conan"]

    def test_conan_cmd_is_list(self) -> None:
        """Test that conan_cmd returns a list."""
        finder = ConanFinder()
        cmd = finder.conan_cmd
        assert isinstance(cmd, list)
        assert len(cmd) >= 1

    def test_uvx_fallback_when_no_conan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test fallback to uvx when conan not in PATH."""
        monkeypatch.delenv("PCONS_CONAN", raising=False)
        monkeypatch.delenv("CONAN", raising=False)

        # Mock shutil.which to simulate conan not found but uvx found
        original_which = shutil.which

        def mock_which(cmd: str) -> str | None:
            if cmd == "conan":
                return None
            if cmd == "uvx":
                return "/usr/bin/uvx"
            if cmd == "uv":
                return "/usr/bin/uv"
            return original_which(cmd)

        monkeypatch.setattr(shutil, "which", mock_which)

        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["uvx", "conan"]


@pytest.mark.skipif(shutil.which("conan") is None, reason="conan not available")
class TestConanFinderIntegration:
    """Integration tests that require conan to be installed."""

    def test_is_available_with_conan(self) -> None:
        """Test is_available when conan is installed."""
        finder = ConanFinder()
        assert finder.is_available() is True

    def test_conan_version_can_be_checked(self) -> None:
        """Test that we can run conan --version."""
        finder = ConanFinder()
        result = finder._run_conan("--version", check=False)
        assert result.returncode == 0
        assert "Conan" in result.stdout or "conan" in result.stdout.lower()
