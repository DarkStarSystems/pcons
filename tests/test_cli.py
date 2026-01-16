# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcons.cli import find_script, setup_logging

if TYPE_CHECKING:
    pass


class TestFindScript:
    """Tests for find_script function."""

    def test_find_existing_script(self, tmp_path: Path) -> None:
        """Test finding an existing script."""
        script = tmp_path / "configure.py"
        script.write_text("# test script")

        result = find_script("configure.py", tmp_path)
        assert result == script

    def test_script_not_found(self, tmp_path: Path) -> None:
        """Test when script doesn't exist."""
        result = find_script("configure.py", tmp_path)
        assert result is None

    def test_find_script_ignores_directories(self, tmp_path: Path) -> None:
        """Test that find_script ignores directories with same name."""
        (tmp_path / "configure.py").mkdir()

        result = find_script("configure.py", tmp_path)
        assert result is None


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_normal(self) -> None:
        """Test normal logging setup."""
        # Just ensure it doesn't crash
        setup_logging(verbose=False, debug=False)

    def test_setup_logging_verbose(self) -> None:
        """Test verbose logging setup."""
        setup_logging(verbose=True, debug=False)

    def test_setup_logging_debug(self) -> None:
        """Test debug logging setup."""
        setup_logging(verbose=False, debug=True)


class TestCLICommands:
    """Tests for CLI commands."""

    def test_pcons_help(self) -> None:
        """Test pcons --help."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "pcons" in result.stdout
        assert "configure" in result.stdout
        assert "generate" in result.stdout
        assert "build" in result.stdout
        assert "clean" in result.stdout

    def test_pcons_version(self) -> None:
        """Test pcons --version."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout

    def test_pcons_init(self, tmp_path: Path) -> None:
        """Test pcons init creates template files."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (tmp_path / "configure.py").exists()
        assert (tmp_path / "build.py").exists()

        # Check content
        configure_content = (tmp_path / "configure.py").read_text()
        assert "Configure" in configure_content
        assert "toolchain" in configure_content

        build_content = (tmp_path / "build.py").read_text()
        assert "Project" in build_content
        assert "NinjaGenerator" in build_content

    def test_pcons_init_force(self, tmp_path: Path) -> None:
        """Test pcons init --force overwrites files."""
        # Create existing files
        (tmp_path / "configure.py").write_text("# old content")
        (tmp_path / "build.py").write_text("# old content")

        # Without --force should fail
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0

        # With --force should succeed
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init", "--force"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        # Check content was replaced
        configure_content = (tmp_path / "configure.py").read_text()
        assert "Configure" in configure_content

    def test_pcons_configure_no_script(self, tmp_path: Path) -> None:
        """Test pcons configure without configure.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "configure"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No configure.py found" in result.stderr

    def test_pcons_generate_no_script(self, tmp_path: Path) -> None:
        """Test pcons generate without build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No build.py found" in result.stderr

    def test_pcons_build_no_ninja(self, tmp_path: Path) -> None:
        """Test pcons build without build.ninja."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No build.ninja found" in result.stderr

    def test_pcons_clean_no_ninja(self, tmp_path: Path) -> None:
        """Test pcons clean without build.ninja (should succeed)."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "clean"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        # Clean with no build.ninja should succeed (nothing to clean)
        assert result.returncode == 0

    def test_pcons_clean_all(self, tmp_path: Path) -> None:
        """Test pcons clean --all removes build directory."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "hello.o").write_text("# fake object file")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "clean", "--all"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert not build_dir.exists()


class TestIntegration:
    """Integration tests for the full build cycle."""

    def test_full_build_cycle(self, tmp_path: Path) -> None:
        """Test a complete build cycle with a simple C program."""
        import shutil

        # Skip if ninja not available
        if shutil.which("ninja") is None:
            pytest.skip("ninja not found")

        # Skip if clang not available
        if shutil.which("clang") is None:
            pytest.skip("clang not found")

        # Create a simple C source file
        hello_c = tmp_path / "hello.c"
        hello_c.write_text(
            """\
#include <stdio.h>

int main(void) {
    printf("Hello, pcons!\\n");
    return 0;
}
"""
        )

        # Create configure.py
        configure_py = tmp_path / "configure.py"
        configure_py.write_text(
            """\
import os
from pathlib import Path
from pcons.configure.config import Configure
from pcons.toolchains import LlvmToolchain, GccToolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
config = Configure(build_dir=build_dir)

llvm = LlvmToolchain()
gcc = GccToolchain()

if llvm.configure(config):
    config.set("toolchain", "llvm")
elif gcc.configure(config):
    config.set("toolchain", "gcc")

config.save()
"""
        )

        # Create build.py
        build_py = tmp_path / "build.py"
        build_py.write_text(
            """\
import os
from pathlib import Path
from pcons.configure.config import Configure
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import GccToolchain, LlvmToolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

config = Configure(build_dir=build_dir)
toolchain_name = config.get("toolchain", "gcc")

if toolchain_name == "llvm":
    toolchain = LlvmToolchain()
else:
    toolchain = GccToolchain()

toolchain.configure(config)

project = Project("hello", root_dir=source_dir, build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

obj = env.cc.Object("hello.o", "hello.c")
env.link.Program("hello", obj)

generator = NinjaGenerator()
generator.generate(project, build_dir)
"""
        )

        # Run configure
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "configure"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, f"configure failed: {result.stderr}"
        assert (tmp_path / "build" / "pcons_config.json").exists()

        # Run generate
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, f"generate failed: {result.stderr}"
        assert (tmp_path / "build" / "build.ninja").exists()

        # Run build
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, f"build failed: {result.stderr}"
        assert (tmp_path / "build" / "hello").exists() or (
            tmp_path / "build" / "hello.exe"
        ).exists()

        # Run the built program
        hello_path = tmp_path / "build" / "hello"
        if not hello_path.exists():
            hello_path = tmp_path / "build" / "hello.exe"

        result = subprocess.run([str(hello_path)], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Hello, pcons!" in result.stdout

        # Run clean
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "clean", "--all"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert not (tmp_path / "build").exists()
