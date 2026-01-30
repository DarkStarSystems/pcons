# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pcons import Generator, MakefileGenerator, NinjaGenerator, get_var, get_variant
from pcons.cli import find_command_in_argv, find_script, parse_variables, setup_logging

if TYPE_CHECKING:
    pass


def _has_c_compiler() -> bool:
    """Check if any C compiler is available."""
    # Unix-style compilers
    if shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"):
        return True
    # Windows compilers
    if sys.platform == "win32":
        if (
            shutil.which("cl.exe")
            or shutil.which("clang-cl.exe")
            or shutil.which("clang-cl")
        ):
            return True
    return False


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
        setup_logging(verbose=False, debug=None)

    def test_setup_logging_verbose(self) -> None:
        """Test verbose logging setup."""
        setup_logging(verbose=True, debug=None)

    def test_setup_logging_debug(self) -> None:
        """Test debug logging setup with subsystem specification."""
        setup_logging(verbose=False, debug="resolve,subst")


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


class TestGenerator:
    """Tests for Generator() function."""

    def test_generator_default_is_ninja(self, monkeypatch) -> None:
        """Test Generator() returns NinjaGenerator by default."""
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.delenv("GENERATOR", raising=False)

        gen = Generator()
        assert isinstance(gen, NinjaGenerator)

    def test_generator_default_parameter(self, monkeypatch) -> None:
        """Test Generator() uses default parameter when not set."""
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.delenv("GENERATOR", raising=False)

        gen = Generator("make")
        assert isinstance(gen, MakefileGenerator)

    def test_generator_from_pcons_generator(self, monkeypatch) -> None:
        """Test Generator() reads from PCONS_GENERATOR (CLI sets this)."""
        monkeypatch.setenv("PCONS_GENERATOR", "make")
        monkeypatch.delenv("GENERATOR", raising=False)

        gen = Generator()
        assert isinstance(gen, MakefileGenerator)

    def test_generator_from_generator_env(self, monkeypatch) -> None:
        """Test Generator() falls back to GENERATOR env var."""
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.setenv("GENERATOR", "make")

        gen = Generator()
        assert isinstance(gen, MakefileGenerator)

    def test_generator_pcons_generator_takes_precedence(self, monkeypatch) -> None:
        """Test PCONS_GENERATOR takes precedence over GENERATOR."""
        monkeypatch.setenv("PCONS_GENERATOR", "ninja")
        monkeypatch.setenv("GENERATOR", "make")

        gen = Generator()
        assert isinstance(gen, NinjaGenerator)

    def test_generator_makefile_alias(self, monkeypatch) -> None:
        """Test 'makefile' is an alias for 'make'."""
        monkeypatch.setenv("PCONS_GENERATOR", "makefile")

        gen = Generator()
        assert isinstance(gen, MakefileGenerator)

    def test_generator_case_insensitive(self, monkeypatch) -> None:
        """Test generator names are case-insensitive."""
        monkeypatch.setenv("PCONS_GENERATOR", "NINJA")

        gen = Generator()
        assert isinstance(gen, NinjaGenerator)

    def test_generator_invalid_raises(self, monkeypatch) -> None:
        """Test Generator() raises ValueError for unknown generator."""
        monkeypatch.setenv("PCONS_GENERATOR", "unknown")

        with pytest.raises(ValueError, match="Unknown generator 'unknown'"):
            Generator()


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


class TestFindCommandInArgv:
    """Tests for find_command_in_argv function."""

    def test_find_command_first_positional(self) -> None:
        """Test finding command as first positional argument."""
        assert find_command_in_argv(["build"]) == "build"
        assert find_command_in_argv(["generate"]) == "generate"
        assert find_command_in_argv(["clean"]) == "clean"
        assert find_command_in_argv(["info"]) == "info"
        assert find_command_in_argv(["init"]) == "init"

    def test_find_command_after_options(self) -> None:
        """Test finding command after flag options."""
        assert find_command_in_argv(["-v", "build"]) == "build"
        assert find_command_in_argv(["--verbose", "generate"]) == "generate"
        # --debug now takes a value, so use = syntax
        assert find_command_in_argv(["--debug=resolve", "generate"]) == "generate"

    def test_find_command_after_option_with_value(self) -> None:
        """Test finding command after options that take values."""
        assert find_command_in_argv(["-B", "mybuild", "build"]) == "build"
        assert find_command_in_argv(["--build-dir", "out", "generate"]) == "generate"
        assert find_command_in_argv(["-j", "4", "build"]) == "build"

    def test_no_command_with_variable(self) -> None:
        """Test that KEY=value is not mistaken for a command."""
        assert find_command_in_argv(["VAR=value"]) is None
        assert find_command_in_argv(["BUILD_PLUGINS=1"]) is None

    def test_no_command_with_options_and_variable(self) -> None:
        """Test no command found when only options and variables present."""
        assert find_command_in_argv(["-B", "build/release", "VAR=1"]) is None
        assert find_command_in_argv(["--verbose", "-B", "out", "FOO=bar"]) is None

    def test_no_command_empty_argv(self) -> None:
        """Test no command when argv is empty."""
        assert find_command_in_argv([]) is None

    def test_no_command_only_options(self) -> None:
        """Test no command when only options are present."""
        assert find_command_in_argv(["-v", "--debug"]) is None
        assert find_command_in_argv(["-B", "build"]) is None  # build is value of -B

    def test_invalid_command_returns_none(self) -> None:
        """Test that invalid commands return None."""
        assert find_command_in_argv(["notacommand"]) is None
        assert find_command_in_argv(["BUILD"]) is None  # case sensitive


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
        # Check version is present (don't hardcode specific version)
        import pcons

        assert pcons.__version__ in result.stdout

    def test_pcons_init(self, tmp_path: Path) -> None:
        """Test pcons init creates template pcons-build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (tmp_path / "pcons-build.py").exists()

        # Check content - should have configure and build together
        build_content = (tmp_path / "pcons-build.py").read_text()
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
        build_py = tmp_path / "pcons-build.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(build_py)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Invalid Python: {result.stderr}"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows doesn't have Unix-style executable permissions",
    )
    def test_pcons_init_creates_executable(self, tmp_path: Path) -> None:
        """Test that init creates an executable file."""
        import stat

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        build_py = tmp_path / "pcons-build.py"
        mode = build_py.stat().st_mode
        assert mode & stat.S_IXUSR, "pcons-build.py should be executable"

    def test_pcons_init_template_runs(self, tmp_path: Path) -> None:
        """Test that the init template can actually run and generate ninja."""
        # Skip if no C compiler available
        if not _has_c_compiler():
            pytest.skip("no C compiler found")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0

        # Run the generated pcons-build.py via pcons generate
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
        (tmp_path / "pcons-build.py").write_text("# old content")

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
        build_content = (tmp_path / "pcons-build.py").read_text()
        assert "Project" in build_content
        assert "Configure" in build_content

    def test_pcons_info(self, tmp_path: Path) -> None:
        """Test pcons info shows pcons-build.py docstring."""
        # Create a pcons-build.py with a docstring
        build_py = tmp_path / "pcons-build.py"
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
        build_py = tmp_path / "pcons-build.py"
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
        """Test pcons info without pcons-build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No pcons-build.py found" in result.stderr

    def test_pcons_generate_no_script(self, tmp_path: Path) -> None:
        """Test pcons generate without pcons-build.py."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No pcons-build.py found" in result.stderr

    def test_pcons_build_no_build_files(self, tmp_path: Path) -> None:
        """Test pcons build without any build files (ninja, make, or xcode)."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No build files found" in result.stderr

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


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing edge cases.

    These tests ensure that KEY=value arguments are not mistaken for commands.
    """

    def test_variable_without_command_no_build_script(self, tmp_path: Path) -> None:
        """Test that VAR=value without a command doesn't error on argument parsing.

        Without pcons-build.py it should fail gracefully, not with 'invalid choice'.
        """
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "FOO=bar"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        # Should fail because no pcons-build.py, not because of argument parsing
        assert result.returncode != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_variable_with_build_dir_option(self, tmp_path: Path) -> None:
        """Test -B option with variable doesn't confuse argument parsing."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-B", "mybuild", "VAR=value"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        # Should fail because no pcons-build.py, not because of argument parsing
        assert result.returncode != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_multiple_variables_without_command(self, tmp_path: Path) -> None:
        """Test multiple KEY=value args without a command."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "FOO=1", "BAR=2", "BAZ=3"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_help_shows_commands(self) -> None:
        """Test that --help shows available commands."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Should show available commands
        assert "info" in result.stdout
        assert "init" in result.stdout
        assert "generate" in result.stdout
        assert "build" in result.stdout
        assert "clean" in result.stdout

    def test_subcommand_help(self) -> None:
        """Test that subcommand --help works."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "build", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "targets" in result.stdout
        assert "--jobs" in result.stdout

    def test_generate_with_variable(self, tmp_path: Path) -> None:
        """Test pcons generate VAR=value works."""
        # Create a minimal pcons-build.py that just prints the variable
        build_py = tmp_path / "pcons-build.py"
        build_py.write_text("""\
import os
from pcons import get_var
print(f"TEST_VAR={get_var('TEST_VAR', 'not_set')}")
""")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate", "TEST_VAR=myvalue"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        # The script will fail (no ninja generation) but should have received the var
        assert "TEST_VAR=myvalue" in result.stdout

    def test_options_before_and_after_command(self) -> None:
        """Test that options work both before and after command."""
        # Options before command
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-v", "build", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "targets" in result.stdout

    def test_info_targets(self, tmp_path: Path) -> None:
        """Test pcons info --targets lists targets by type."""
        build_py = tmp_path / "pcons-build.py"
        build_py.write_text("""\
import os
from pathlib import Path
from pcons.core.project import Project

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))
project = Project("test", root_dir=source_dir, build_dir=build_dir)
env = project.Environment()

hello = env.Command(target="hello.txt", source="hello.in", command="cp $SOURCE $TARGET")
project.Alias("all", hello)
""")
        (tmp_path / "hello.in").write_text("hi")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "info", "--targets"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Aliases:" in result.stdout
        assert "all" in result.stdout
        assert "Targets:" in result.stdout
        assert "[command]" in result.stdout
        assert "hello.txt" in result.stdout


class TestIntegration:
    """Integration tests for the full build cycle."""

    def test_full_build_cycle(self, tmp_path: Path) -> None:
        """Test a complete build cycle with a simple C program."""
        # Skip if ninja not available
        if shutil.which("ninja") is None:
            pytest.skip("ninja not found")

        # Skip if no C compiler available
        if not _has_c_compiler():
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

        # Create pcons-build.py (configuration is done inline)
        build_py = tmp_path / "pcons-build.py"
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
generator.generate(project)
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
