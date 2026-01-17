# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcons import get_var, get_variant
from pcons.cli import find_script, parse_variables, setup_logging

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


class TestGetVar:
    """Tests for get_var and get_variant functions."""

    def test_get_var_default(self, monkeypatch) -> None:
        """Test get_var returns default when not set."""
        # Clear any cached vars
        import pcons
        pcons._cli_vars = None
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", "default_value") == "default_value"

    def test_get_var_from_env(self, monkeypatch) -> None:
        """Test get_var reads from environment variable."""
        import pcons
        pcons._cli_vars = None
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.setenv("TEST_VAR", "env_value")

        assert get_var("TEST_VAR", "default") == "env_value"

    def test_get_var_from_pcons_vars(self, monkeypatch) -> None:
        """Test get_var reads from PCONS_VARS JSON."""
        import pcons
        pcons._cli_vars = None
        monkeypatch.setenv("PCONS_VARS", '{"TEST_VAR": "cli_value"}')
        monkeypatch.setenv("TEST_VAR", "env_value")  # Should be overridden

        assert get_var("TEST_VAR", "default") == "cli_value"

    def test_get_variant_default(self, monkeypatch) -> None:
        """Test get_variant returns default when not set."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "release"

    def test_get_variant_from_pcons_variant(self, monkeypatch) -> None:
        """Test get_variant reads from PCONS_VARIANT (CLI sets this)."""
        monkeypatch.setenv("PCONS_VARIANT", "debug")
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "debug"

    def test_get_variant_from_variant_env(self, monkeypatch) -> None:
        """Test get_variant falls back to VARIANT env var."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("release") == "debug"

    def test_get_variant_pcons_variant_takes_precedence(self, monkeypatch) -> None:
        """Test PCONS_VARIANT takes precedence over VARIANT."""
        monkeypatch.setenv("PCONS_VARIANT", "release")
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("default") == "release"


class TestParseVariables:
    """Tests for parse_variables function."""

    def test_parse_simple_variable(self) -> None:
        """Test parsing a simple KEY=value variable."""
        variables, remaining = parse_variables(["PORT=ofx"])
        assert variables == {"PORT": "ofx"}
        assert remaining == []

    def test_parse_multiple_variables(self) -> None:
        """Test parsing multiple KEY=value variables."""
        variables, remaining = parse_variables(["PORT=ofx", "CC=clang", "USE_CUDA=1"])
        assert variables == {"PORT": "ofx", "CC": "clang", "USE_CUDA": "1"}
        assert remaining == []

    def test_parse_empty_value(self) -> None:
        """Test parsing KEY= (empty value)."""
        variables, remaining = parse_variables(["EMPTY="])
        assert variables == {"EMPTY": ""}
        assert remaining == []

    def test_parse_value_with_equals(self) -> None:
        """Test parsing KEY=value=with=equals."""
        variables, remaining = parse_variables(["FLAGS=-O2 -DFOO=1"])
        assert variables == {"FLAGS": "-O2 -DFOO=1"}
        assert remaining == []

    def test_parse_mixed_args(self) -> None:
        """Test parsing a mix of variables and targets."""
        variables, remaining = parse_variables(["PORT=ofx", "all", "test", "CC=gcc"])
        assert variables == {"PORT": "ofx", "CC": "gcc"}
        assert remaining == ["all", "test"]

    def test_parse_flags_not_variables(self) -> None:
        """Test that flags starting with - are not treated as variables."""
        variables, remaining = parse_variables(["-v", "--debug", "PORT=ofx"])
        assert variables == {"PORT": "ofx"}
        assert remaining == ["-v", "--debug"]

    def test_parse_empty_key(self) -> None:
        """Test that =value (empty key) is not parsed as a variable."""
        variables, remaining = parse_variables(["=value"])
        assert variables == {}
        assert remaining == ["=value"]


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
        assert "generate" in result.stdout
        assert "build" in result.stdout
        assert "clean" in result.stdout
        assert "init" in result.stdout

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
        """Test pcons init creates template build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (tmp_path / "build.py").exists()

        # Check content - should have configure and build together
        build_content = (tmp_path / "build.py").read_text()
        assert "Project" in build_content
        assert "NinjaGenerator" in build_content
        assert "Configure" in build_content
        assert "get_variant" in build_content
        assert "get_var" in build_content
        assert "PCONS_BUILD_DIR" in build_content
        assert "PCONS_RECONFIGURE" in build_content

    def test_pcons_init_creates_valid_python(self, tmp_path: Path) -> None:
        """Test that init creates syntactically valid Python."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        # Verify it's valid Python by compiling it
        build_py = tmp_path / "build.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(build_py)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Invalid Python: {result.stderr}"

    def test_pcons_init_creates_executable(self, tmp_path: Path) -> None:
        """Test that init creates an executable file."""
        import os
        import stat

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        build_py = tmp_path / "build.py"
        mode = build_py.stat().st_mode
        assert mode & stat.S_IXUSR, "build.py should be executable"

    def test_pcons_init_template_runs(self, tmp_path: Path) -> None:
        """Test that the init template can actually run and generate ninja."""
        import shutil

        # Skip if no C compiler available
        if shutil.which("clang") is None and shutil.which("gcc") is None:
            pytest.skip("no C compiler found")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        # Run the generated build.py via pcons generate
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, f"generate failed: {result.stderr}"
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_pcons_init_force(self, tmp_path: Path) -> None:
        """Test pcons init --force overwrites files."""
        # Create existing file
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
        build_content = (tmp_path / "build.py").read_text()
        assert "Project" in build_content
        assert "Configure" in build_content

    def test_pcons_info(self, tmp_path: Path) -> None:
        """Test pcons info shows build.py docstring."""
        # Create a build.py with a docstring
        build_py = tmp_path / "build.py"
        build_py.write_text('''"""My project build script.

Variables:
    FOO - Some variable (default: bar)
"""
print("hello")
''')

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "My project build script" in result.stdout
        assert "FOO" in result.stdout

    def test_pcons_info_no_docstring(self, tmp_path: Path) -> None:
        """Test pcons info handles missing docstring gracefully."""
        build_py = tmp_path / "build.py"
        build_py.write_text('print("hello")\n')

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "No docstring found" in result.stdout

    def test_pcons_info_no_script(self, tmp_path: Path) -> None:
        """Test pcons info without build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No build.py found" in result.stderr

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
        if shutil.which("clang") is None and shutil.which("gcc") is None:
            pytest.skip("no C compiler found")

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

        # Create build.py (configuration is done inline)
        build_py = tmp_path / "build.py"
        build_py.write_text(
            """\
import os
from pathlib import Path
from pcons.configure.config import Configure
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

# Configuration (auto-cached)
config = Configure(build_dir=build_dir)
if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    toolchain = find_c_toolchain()
    toolchain.configure(config)
    config.set("configured", True)
    config.save()

# Create project
project = Project("hello", root_dir=source_dir, build_dir=build_dir)
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)

obj = env.cc.Object("hello.o", "hello.c")
env.link.Program("hello", obj)

generator = NinjaGenerator()
generator.generate(project, build_dir)
"""
        )

        # Run generate (which includes configuration)
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
