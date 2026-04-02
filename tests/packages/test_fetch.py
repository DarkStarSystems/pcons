# SPDX-License-Identifier: MIT
"""Tests for pcons-fetch CLI."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.fetch.cli import (
    download_source,
    fetch_package,
    generate_package_description,
    load_deps_file,
    setup_logging,
)


class TestSetupLogging:
    """Tests for setup_logging."""

    def test_setup_logging_normal(self) -> None:
        """Test normal logging setup."""
        setup_logging(verbose=False, debug=False)

    def test_setup_logging_verbose(self) -> None:
        """Test verbose logging setup."""
        setup_logging(verbose=True, debug=False)

    def test_setup_logging_debug(self) -> None:
        """Test debug logging setup."""
        setup_logging(verbose=False, debug=True)


class TestLoadDepsFile:
    """Tests for load_deps_file."""

    def test_load_valid_deps_file(self, tmp_path: Path) -> None:
        """Test loading a valid deps.toml file."""
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            """\
[packages.zlib]
url = "https://github.com/madler/zlib.git"
version = "1.2.13"
build = "cmake"
"""
        )

        data = load_deps_file(deps_file)
        assert "packages" in data
        assert "zlib" in data["packages"]
        assert data["packages"]["zlib"]["version"] == "1.2.13"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test loading a non-existent file."""
        with pytest.raises(FileNotFoundError):
            load_deps_file(tmp_path / "nonexistent.toml")


class TestGeneratePackageDescription:
    """Tests for generate_package_description."""

    def test_generate_with_include_and_lib(self, tmp_path: Path) -> None:
        """Test generating description with include and lib dirs."""
        install_prefix = tmp_path / "install"
        include_dir = install_prefix / "include"
        lib_dir = install_prefix / "lib"

        include_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)

        # Create some fake libraries
        (lib_dir / "libtest.a").write_text("")
        (lib_dir / "libfoo.so").write_text("")

        pkg, pc_files = generate_package_description(
            name="mylib",
            version="1.0",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert pc_files == []
        assert pkg.name == "mylib"
        assert pkg.version == "1.0"
        assert str(include_dir.resolve()) in pkg.include_dirs
        assert str(lib_dir.resolve()) in pkg.library_dirs
        assert "test" in pkg.libraries
        assert "foo" in pkg.libraries
        assert "pcons-fetch" in pkg.found_by

    def test_generate_empty_install(self, tmp_path: Path) -> None:
        """Test generating description with empty install prefix."""
        install_prefix = tmp_path / "empty_install"
        install_prefix.mkdir()

        pkg, pc_files = generate_package_description(
            name="empty",
            version="0.1",
            install_prefix=install_prefix,
            build_system="autotools",
        )

        assert pc_files == []
        assert pkg.name == "empty"
        assert pkg.include_dirs == []
        assert pkg.libraries == []

    def test_generate_prefers_pc_files(self, tmp_path: Path) -> None:
        """When .pc files exist, return them instead of scanning libs."""
        install_prefix = tmp_path / "install"
        lib_dir = install_prefix / "lib"
        pc_dir = lib_dir / "pkgconfig"
        pc_dir.mkdir(parents=True)
        (pc_dir / "mylib.pc").write_text("Name: mylib\nVersion: 1.0\n")
        # Also create a library — should be ignored when .pc exists
        (lib_dir / "libmylib.a").write_text("")

        pkg, pc_files = generate_package_description(
            name="mylib",
            version="1.0",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert len(pc_files) == 1
        assert pc_files[0].name == "mylib.pc"
        # When .pc files found, paths/link sections should be empty
        assert pkg.include_dirs == []
        assert pkg.libraries == []

    def test_generate_skips_symlinks_and_versioned_libs(self, tmp_path: Path) -> None:
        """Versioned dylib symlinks should not produce duplicate library names."""
        install_prefix = tmp_path / "install"
        lib_dir = install_prefix / "lib"
        lib_dir.mkdir(parents=True)

        # Real file
        (lib_dir / "libz.1.3.1.dylib").write_text("")
        # Symlinks
        (lib_dir / "libz.1.dylib").symlink_to("libz.1.3.1.dylib")
        (lib_dir / "libz.dylib").symlink_to("libz.1.dylib")
        # Static lib (real file)
        (lib_dir / "libz.a").write_text("")

        pkg, _ = generate_package_description(
            name="zlib",
            version="1.3.1",
            install_prefix=install_prefix,
            build_system="cmake",
        )

        assert pkg.libraries == ["z"]


class TestCLICommands:
    """Tests for CLI commands."""

    def test_help(self) -> None:
        """Test pcons-fetch --help."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.packages.fetch.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "pcons-fetch" in result.stdout

    def test_version(self) -> None:
        """Test pcons-fetch --version."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.packages.fetch.cli", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Check version is present (don't hardcode specific version)
        import pcons

        assert pcons.__version__ in result.stdout

    def test_list_no_deps_file(self, tmp_path: Path) -> None:
        """Test pcons-fetch list with no deps file."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.packages.fetch.cli", "list"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_list_with_deps_file(self, tmp_path: Path) -> None:
        """Test pcons-fetch list with a deps file."""
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text(
            """\
[packages.zlib]
url = "https://github.com/madler/zlib.git"
version = "1.2.13"
build = "cmake"

[packages.openssl]
url = "https://github.com/openssl/openssl.git"
version = "3.0"
build = "autotools"
"""
        )

        result = subprocess.run(
            [sys.executable, "-m", "pcons.packages.fetch.cli", "list", str(deps_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "zlib" in result.stdout
        assert "1.2.13" in result.stdout
        assert "openssl" in result.stdout
        assert "cmake" in result.stdout
        assert "autotools" in result.stdout

    def test_clean_nonexistent_dir(self, tmp_path: Path) -> None:
        """Test pcons-fetch clean with non-existent dir."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.packages.fetch.cli",
                "clean",
                "--deps-dir",
                str(tmp_path / "nonexistent"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_clean_build_dir(self, tmp_path: Path) -> None:
        """Test pcons-fetch clean removes build dir."""
        deps_dir = tmp_path / ".deps"
        build_dir = deps_dir / "build"
        build_dir.mkdir(parents=True)
        (build_dir / "testfile").write_text("test")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.packages.fetch.cli",
                "clean",
                "--deps-dir",
                str(deps_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert not build_dir.exists()
        assert deps_dir.exists()  # Parent should still exist

    def test_clean_all(self, tmp_path: Path) -> None:
        """Test pcons-fetch clean --all removes everything."""
        deps_dir = tmp_path / ".deps"
        deps_dir.mkdir()
        (deps_dir / "testfile").write_text("test")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.packages.fetch.cli",
                "clean",
                "--all",
                "--deps-dir",
                str(deps_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert not deps_dir.exists()

    def test_fetch_no_deps_file(self, tmp_path: Path) -> None:
        """Test pcons-fetch fetch with no deps file."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.packages.fetch.cli",
                "fetch",
                str(tmp_path / "nonexistent.toml"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_fetch_empty_packages(self, tmp_path: Path) -> None:
        """Test pcons-fetch fetch with empty packages list."""
        deps_file = tmp_path / "deps.toml"
        deps_file.write_text("[packages]\n")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.packages.fetch.cli",
                "fetch",
                str(deps_file),
            ],
            capture_output=True,
            text=True,
        )
        # Should succeed with warning
        assert result.returncode == 0


class TestDownloadSource:
    """Tests for source download helpers."""

    def test_git_ssh_url_not_split_as_ref(self, tmp_path: Path) -> None:
        """SCP-style SSH URLs should remain intact."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            source_dir = download_source(
                "git@github.com:org/repo.git", tmp_path, "repo"
            )

        assert source_dir == tmp_path / "repo"
        assert commands == [
            [
                "git",
                "clone",
                "--depth=1",
                "git@github.com:org/repo.git",
                str(source_dir),
            ]
        ]

    def test_git_https_url_with_ref_uses_branch(self, tmp_path: Path) -> None:
        """HTTP(S) URLs may append a ref using @ref syntax."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            download_source("git+https://example.com/repo.git@v1.2.3", tmp_path, "repo")

        assert commands == [
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                "v1.2.3",
                "https://example.com/repo.git",
                str(tmp_path / "repo"),
            ]
        ]

    def test_git_url_with_ref_and_dotgit_detected(self, tmp_path: Path) -> None:
        """URLs like https://...repo.git@main should be detected as git."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            download_source("https://github.com/org/repo.git@main", tmp_path, "repo")

        # Should clone with --branch main, not try to download as archive
        assert commands[0][:2] == ["git", "clone"]
        assert "--branch" in commands[0]
        assert "main" in commands[0]

    def test_git_clone_with_commit_sha(self, tmp_path: Path) -> None:
        """Commit SHAs should do full clone + checkout, not --branch."""
        commands: list[list[str]] = []

        def mock_run(cmd, **kwargs):
            commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        with patch("subprocess.run", side_effect=mock_run):
            download_source(f"git+https://example.com/repo.git@{sha}", tmp_path, "repo")

        # First command: full clone (no --depth=1, no --branch)
        assert commands[0][:2] == ["git", "clone"]
        assert "--depth=1" not in commands[0]
        assert "--branch" not in commands[0]
        # Second command: checkout the SHA
        assert commands[1] == ["git", "-C", str(tmp_path / "repo"), "checkout", sha]

    def test_zip_rejects_path_traversal(self, tmp_path: Path) -> None:
        """Zip extraction must reject ../ traversal."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("../escape.txt", "owned")

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="escapes extraction root"),
        ):
            download_source("https://example.com/payload.zip", tmp_path / "dest", "pkg")

        assert not (tmp_path / "dest" / "escape.txt").exists()
        assert not (tmp_path / "escape.txt").exists()

    def test_tar_rejects_symlinks(self, tmp_path: Path) -> None:
        """Tar extraction must reject symlinks."""
        archive_path = tmp_path / "payload.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            info = tarfile.TarInfo("link.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "../escape.txt"
            tf.addfile(info)

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="Refusing to extract link"),
        ):
            download_source(
                "https://example.com/payload.tar.gz", tmp_path / "dest", "pkg"
            )

    def test_archive_sha256_mismatch_fails(self, tmp_path: Path) -> None:
        """Downloaded archives must match the requested SHA-256."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            pytest.raises(RuntimeError, match="SHA-256 mismatch"),
        ):
            download_source(
                "https://example.com/payload.zip",
                tmp_path / "dest",
                "pkg",
                sha256="0" * 64,
            )

    def test_archive_sha256_match_succeeds(self, tmp_path: Path) -> None:
        """Matching SHA-256 should allow extraction to proceed."""
        archive_path = tmp_path / "payload.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()

        def fake_urlretrieve(url, dest):
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            source_dir = download_source(
                "https://example.com/payload.zip",
                tmp_path / "dest",
                "pkg",
                sha256=digest,
            )

        assert source_dir == tmp_path / "dest" / "pkg"
        assert (source_dir / "file.txt").read_text() == "ok"


class TestFetchPackage:
    """Tests for the fetch_package end-to-end flow."""

    def test_fetch_package_cmake_from_archive(self, tmp_path: Path) -> None:
        """Test full fetch_package pipeline: download archive, build, generate .pcons-pkg.toml."""
        # Create a zip archive with a fake CMakeLists.txt
        archive_path = tmp_path / "source.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr(
                "mylib/CMakeLists.txt", "cmake_minimum_required(VERSION 3.10)\n"
            )
            zf.writestr("mylib/src/lib.c", "int mylib_init(void) { return 0; }\n")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        deps_dir = tmp_path / ".deps"
        output_dir = tmp_path / "output"
        install_prefix = deps_dir / "install"

        def fake_cmake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            """Simulate cmake: on --install, create include/ and lib/ dirs."""
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            # When cmake --install is called, create the install tree
            if "--install" in cmd:
                inc = install_prefix / "include"
                lib = install_prefix / "lib"
                inc.mkdir(parents=True, exist_ok=True)
                lib.mkdir(parents=True, exist_ok=True)
                (inc / "mylib.h").write_text("#pragma once\nint mylib_init(void);\n")
                (lib / "libmylib.a").write_text("")
            return result

        pkg_config = {
            "url": "https://example.com/mylib-1.0.zip",
            "version": "1.0",
            "build": "cmake",
        }

        with (
            patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve),
            patch("shutil.which", return_value="/usr/bin/cmake"),
            patch("subprocess.run", side_effect=fake_cmake_run),
        ):
            ok = fetch_package("mylib", pkg_config, deps_dir, output_dir)

        assert ok
        pkg_file = output_dir / "mylib.pcons-pkg.toml"
        assert pkg_file.exists()

        data = tomllib.loads(pkg_file.read_text())
        assert data["package"]["name"] == "mylib"
        assert data["package"]["version"] == "1.0"
        assert any("include" in d for d in data["paths"]["include_dirs"])
        assert "mylib" in data["link"]["libraries"]

    def test_fetch_package_missing_url(self, tmp_path: Path) -> None:
        """fetch_package should fail when no URL is provided."""
        deps_dir = tmp_path / ".deps"
        output_dir = tmp_path / "output"
        ok = fetch_package("bad", {"version": "1.0"}, deps_dir, output_dir)
        assert not ok

    def test_fetch_package_unknown_build_system(self, tmp_path: Path) -> None:
        """fetch_package should fail for an unknown build system."""
        archive_path = tmp_path / "source.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("pkg/file.txt", "ok")

        def fake_urlretrieve(url: str, dest: str) -> tuple[str, None]:
            Path(dest).write_bytes(archive_path.read_bytes())
            return str(dest), None

        pkg_config = {
            "url": "https://example.com/pkg.zip",
            "version": "1.0",
            "build": "meson",  # unsupported
        }
        with patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
            ok = fetch_package("pkg", pkg_config, tmp_path / ".deps", tmp_path / "out")
        assert not ok
