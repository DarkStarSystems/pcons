# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcons import (
    Generator,
    MakefileGenerator,
    MetadataGenerator,
    MultiGenerator,
    NinjaGenerator,
)
from pcons.cli import (
    _find_command_index,
    find_command_in_argv,
    find_script,
    parse_variables,
    run_script,
    setup_logging,
)
from pcons.core.vars import _clear_cli_vars


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

    def test_generator_metadata(self, monkeypatch) -> None:
        """Test Generator() supports metadata generator."""
        monkeypatch.setenv("PCONS_GENERATOR", "metadata")

        gen = Generator()
        assert isinstance(gen, MetadataGenerator)

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

    def test_generator_multi_via_env(self, monkeypatch) -> None:
        """Test colon-separated PCONS_GENERATOR returns MultiGenerator."""
        monkeypatch.setenv("PCONS_GENERATOR", "ninja:metadata")
        monkeypatch.delenv("GENERATOR", raising=False)

        gen = Generator()
        assert isinstance(gen, MultiGenerator)
        assert gen.name == "ninja:metadata"
        assert isinstance(gen._generators[0], NinjaGenerator)
        assert isinstance(gen._generators[1], MetadataGenerator)

    def test_generator_multi_invalid_raises(self, monkeypatch) -> None:
        """Test colon-separated PCONS_GENERATOR raises for unknown name."""
        monkeypatch.setenv("PCONS_GENERATOR", "ninja:unknown")

        with pytest.raises(ValueError, match="Unknown generator 'unknown'"):
            Generator()

    def test_generator_single_not_wrapped(self, monkeypatch) -> None:
        """Test a single-name PCONS_GENERATOR is not wrapped in MultiGenerator."""
        monkeypatch.setenv("PCONS_GENERATOR", "ninja")
        monkeypatch.delenv("GENERATOR", raising=False)

        gen = Generator()
        assert not isinstance(gen, MultiGenerator)
        assert isinstance(gen, NinjaGenerator)


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


class TestFindCommandIndex:
    """Tests for _find_command_index (returns the position, not the name)."""

    def test_index_of_first_positional_command(self) -> None:
        assert _find_command_index(["build"]) == 0
        assert _find_command_index(["-v", "generate"]) == 1

    def test_option_value_equal_to_command_is_not_the_command(self) -> None:
        # The motivating bug: an option value that equals a command name
        # must not be reported as the subcommand.
        assert _find_command_index(["--build-dir", "test", "test"]) == 2
        assert _find_command_index(["-B", "build", "build"]) == 2

    def test_equals_option_then_command(self) -> None:
        assert _find_command_index(["--build-dir=out", "test"]) == 1

    def test_boolean_flag_then_command(self) -> None:
        assert _find_command_index(["--unknown-flag", "clean"]) == 1

    def test_no_command_returns_none(self) -> None:
        assert _find_command_index([]) is None
        assert _find_command_index(["notacommand"]) is None
        assert _find_command_index(["--build-dir", "out"]) is None


class TestRunScriptEnvironment:
    """Tests for run_script environment handling."""

    def test_run_script_restores_previous_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-existing PCONS environment should be restored after the run."""
        import os

        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.setenv("PCONS_BUILD_DIR", "original-build")
        monkeypatch.setenv("PCONS_GENERATOR", "original-generator")
        monkeypatch.setenv("CUSTOM_ENV", "original-custom")
        _clear_cli_vars()

        exit_code, projects = run_script(
            script,
            tmp_path / "build",
            variables={"FOO": "BAR"},
            generator="ninja",
            extra_env={"CUSTOM_ENV": "override"},
        )

        assert exit_code == 0
        assert len(projects) == 1
        assert os.environ["PCONS_BUILD_DIR"] == "original-build"
        assert os.environ["PCONS_GENERATOR"] == "original-generator"
        assert os.environ["CUSTOM_ENV"] == "original-custom"
        assert "PCONS_VARS" not in os.environ

    def test_run_script_generator_list_joins_with_colon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_script with a list of generators sets PCONS_GENERATOR as colon-joined."""

        script = tmp_path / "pcons-build.py"
        script.write_text(
            "import os\n"
            "from pcons import Project\n"
            "val = os.environ.get('PCONS_GENERATOR', '')\n"
            "assert val == 'ninja:metadata', f'Got {val!r}'\n"
            "Project('demo')\n"
        )

        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        _clear_cli_vars()

        exit_code, _ = run_script(
            script, tmp_path / "build", generator=["ninja", "metadata"]
        )
        assert exit_code == 0

    def test_run_script_cleans_up_new_environment_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keys created only for the script run should be removed afterwards."""
        import os

        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("CUSTOM_ENV", raising=False)

        exit_code, _ = run_script(
            script,
            tmp_path / "build",
            variant="debug",
            extra_env={"CUSTOM_ENV": "temp"},
        )

        assert exit_code == 0
        assert "PCONS_BUILD_DIR" not in os.environ
        assert "PCONS_VARIANT" not in os.environ
        assert "CUSTOM_ENV" not in os.environ


class TestDirectoryArg:
    """Tests for -C/--directory argument."""

    def test_dash_c_changes_directory(self, tmp_path: Path) -> None:
        """Test that -C changes to the specified directory."""
        # Create a pcons-build.py in a subdirectory
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / "pcons-build.py").write_text('"""Test project."""\nprint("ok")\n')

        # Run pcons from tmp_path with -C myproject
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-C", str(subdir), "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Test project" in result.stdout

    def test_long_form_directory(self, tmp_path: Path) -> None:
        """Test --directory=DIR form."""
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / "pcons-build.py").write_text('"""Long form test."""\nprint("ok")\n')

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", f"--directory={subdir}", "info"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Long form test" in result.stdout

    def test_dash_c_invalid_directory(self, tmp_path: Path) -> None:
        """Test -C with non-existent directory."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-C", str(tmp_path / "nope"), "info"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "error" in result.stderr

    def test_dash_c_missing_arg(self) -> None:
        """Test -C without a directory argument."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-C"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "requires an argument" in result.stderr

    def test_dash_c_init(self, tmp_path: Path) -> None:
        """Test -C works with init command."""
        subdir = tmp_path / "newproject"
        subdir.mkdir()

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "-C", str(subdir), "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (subdir / "pcons-build.py").exists()
        # Should NOT exist in the original directory
        assert not (tmp_path / "pcons-build.py").exists()


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
        """Test pcons init in an empty dir scaffolds a working starter."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (tmp_path / "pcons-build.py").exists()
        # Empty dir: a hello-world starter source is created
        assert (tmp_path / "src" / "main.c").exists()

        # Check content uses the canonical pcons API
        build_content = (tmp_path / "pcons-build.py").read_text()
        assert "from pcons import Project, find_c_toolchain" in build_content
        # No explicit generate call needed: generation is automatic
        assert ".generate(" not in build_content
        # Project and program named after the directory
        assert f'Project("{tmp_path.name}")' in build_content
        assert '"src/main.c",' in build_content
        # Should NOT use internal imports or legacy boilerplate
        assert "NinjaGenerator" not in build_content
        assert "Generator()" not in build_content
        assert "from pcons.core" not in build_content
        assert "from pcons.generators" not in build_content

    def test_pcons_init_lang_cpp(self, tmp_path: Path) -> None:
        """Test pcons init --lang cpp scaffolds a C++ starter."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init", "--lang", "cpp"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert (tmp_path / "src" / "main.cpp").exists()
        assert '"src/main.cpp",' in (tmp_path / "pcons-build.py").read_text()

    def test_pcons_init_adopts_existing_sources(self, tmp_path: Path) -> None:
        """Test pcons init generates a target from existing sources."""
        (tmp_path / "src" / "util").mkdir(parents=True)
        (tmp_path / "include").mkdir()
        (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
        (tmp_path / "src" / "util" / "helper.cpp").write_text("void helper() {}\n")
        (tmp_path / "include" / "helper.h").write_text("void helper();\n")

        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "init"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        # No starter source is scaffolded over existing code
        assert not (tmp_path / "src" / "main.c").exists()

        build_content = (tmp_path / "pcons-build.py").read_text()
        assert '"src/main.cpp",' in build_content
        assert '"src/util/helper.cpp",' in build_content
        assert 'include_dirs.append("include")' in build_content

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

    def test_auto_generate_without_generate_call(self, tmp_path: Path) -> None:
        """A script with no generate call auto-generates, even run directly."""
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\nproject = Project('auto')\n"
        )
        result = subprocess.run(
            [sys.executable, "pcons-build.py"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_no_auto_generate_on_script_crash(self, tmp_path: Path) -> None:
        """A crashed script must not generate build files at exit."""
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\n"
            "project = Project('crash')\n"
            "raise RuntimeError('boom')\n"
        )
        result = subprocess.run(
            [sys.executable, "pcons-build.py"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "boom" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_no_auto_generate_on_sys_exit_via_cli(self, tmp_path: Path) -> None:
        """A script that sys.exit()s nonzero under the CLI must not generate."""
        (tmp_path / "pcons-build.py").write_text(
            "import sys\n"
            "from pcons import Project\n"
            "project = Project('bail')\n"
            "sys.exit(3)\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 3
        assert not (tmp_path / "build" / "build.ninja").exists()

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
        assert "from pcons import Project, find_c_toolchain" in build_content

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

    def test_main_entry_point_propagates_exit_code(self, tmp_path: Path) -> None:
        """__main__.py must call sys.exit(main()) so build failures propagate."""
        result = subprocess.run(
            [sys.executable, "-m", "pcons", "build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0

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

    def test_test_subcommand_dispatches_to_runner(self, tmp_path: Path) -> None:
        """`pcons test` dispatches to pcons.test_runner without argparse."""
        # Hand-build a manifest so the runner has something to operate on.
        import json as _json

        manifest = tmp_path / "tests.json"
        manifest.write_text(
            _json.dumps(
                {
                    "version": 1,
                    "project": "cli_dispatch",
                    "build_dir": str(tmp_path),
                    "tests": [
                        {
                            "name": "demo",
                            "command": ["/bin/true"],
                            "labels": ["unit"],
                        }
                    ],
                }
            )
        )
        # --list returns 0 without executing; that's enough to confirm
        # the dispatch path reached the runner.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.cli",
                "test",
                "--manifest",
                str(manifest),
                "--list",
                "--no-color",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "demo" in result.stdout

    def test_test_dispatch_not_confused_by_option_value(self, tmp_path: Path) -> None:
        """An option VALUE equal to 'test' must not be mistaken for the subcommand.

        `pcons --build-dir test test ...` has "test" appearing twice: once
        as the value of --build-dir, once as the actual subcommand. Locating
        the dispatch point by scanning raw argv for the literal string
        "test" (sys.argv.index("test")) finds the option value first and
        hands the runner a bogus leading "test" positional, which its
        argparse rejects. Dispatch must instead reuse the same
        skip-options-and-their-values logic as find_command_in_argv.
        """
        import json as _json

        manifest = tmp_path / "tests.json"
        manifest.write_text(
            _json.dumps(
                {
                    "version": 1,
                    "project": "cli_dispatch",
                    "build_dir": str(tmp_path),
                    "tests": [
                        {
                            "name": "demo",
                            "command": ["/bin/true"],
                            "labels": ["unit"],
                        }
                    ],
                }
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.cli",
                "--build-dir",
                "test",
                "test",
                "--manifest",
                str(manifest),
                "--list",
                "--no-color",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "demo" in result.stdout

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
