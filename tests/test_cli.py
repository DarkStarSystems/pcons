# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from pcons import (
    Generator,
    MakefileGenerator,
    MetadataGenerator,
    MultiGenerator,
    NinjaGenerator,
)
from pcons.cli import (
    cli,
    cmd_cache,
    find_script,
    parse_variables,
    run_script,
    setup_logging,
)
from pcons.cli import (
    main as cli_main,
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


def _capture_command(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> list[argparse.Namespace]:
    """Stand in for a cmd_* entry point so a test can read what it was handed."""
    seen: list[argparse.Namespace] = []

    def fake(args: argparse.Namespace) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(f"pcons.cli.{name}", fake)
    return seen


def _capture_test_runner(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stand in for the test runner and record the argv the CLI forwards."""
    seen: list[list[str]] = []

    def fake(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("pcons.test_runner.main", fake)
    return seen


def _invoke(*argv: str) -> Result:
    """Run the CLI in this process and return click's Result.

    catch_exceptions=False: otherwise a crash inside a command lands in
    result.exception and the test reads as passing.

    The commands call logging.basicConfig(force=True), which binds a handler
    to whatever sys.stderr is at the time, here the runner's capture buffer.
    The handlers are restored so that buffer does not swallow the log output
    of every later test in the session.
    """
    handlers = logging.root.handlers[:]
    level = logging.root.level
    try:
        return CliRunner().invoke(cli, list(argv), catch_exceptions=False)
    finally:
        logging.root.handlers[:] = handlers
        logging.root.setLevel(level)


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

    def test_run_script_persists_vars_across_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A var configured in one run is readable by a later bare run."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        # Second run has no CLI vars; get_var must read the persisted value.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "val = pcons.get_var('MY_VAR')\n"
            "assert val == '42', f'Got {val!r}'\n"
            "Project('demo')\n"
        )

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("MY_VAR", raising=False)
        _clear_cli_vars()

        # First run: configure MY_VAR=42.
        exit_code, _ = run_script(script, build_dir, variables={"MY_VAR": "42"})
        assert exit_code == 0

        # Second run: no CLI vars -> reads persisted 42.
        exit_code, _ = run_script(script, build_dir)
        assert exit_code == 0

    def test_env_var_beats_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-named env var wins over the persisted cache (precedence trap).

        Folding the cache into PCONS_VARS naively would invert env > cache, since
        get_var checks PCONS_VARS before the environment. run_script must leave an
        env-shadowed cached var out of PCONS_VARS so the env value still wins.
        """
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("MY_VAR", raising=False)
        _clear_cli_vars()

        # First run persists MY_VAR=42 (no assertion).
        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, variables={"MY_VAR": "42"})[0] == 0

        # Second run: a same-named env var must win over the cached 42.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "val = pcons.get_var('MY_VAR')\n"
            "assert val == '7', f'Got {val!r}'\n"
            "Project('demo')\n"
        )
        monkeypatch.setenv("MY_VAR", "7")
        assert run_script(script, build_dir)[0] == 0

        # And the env override must not have rewritten the cache to 7.
        assert self._persisted_var(build_dir, "MY_VAR") == "42"

    def _persisted_var(self, build_dir: Path, name: str) -> str | None:
        import json

        from pcons.core.cache import CACHE_FILE

        cache_file = build_dir / CACHE_FILE
        if not cache_file.exists():
            return None
        return json.loads(cache_file.read_text()).get("vars", {}).get(name)

    def test_run_script_persists_variant_and_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--variant and -G configured in one run are reused by a later bare run."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "variant = pcons.get_variant()\n"
            "assert variant == 'debug', f'Got {variant!r}'\n"
            "assert isinstance(pcons.Generator(), pcons.MakefileGenerator)\n"
            "Project('demo')\n"
        )

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("VARIANT", raising=False)
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.delenv("GENERATOR", raising=False)
        _clear_cli_vars()

        # First run: configure variant=debug, generator=make.
        exit_code, _ = run_script(script, build_dir, variant="debug", generator="make")
        assert exit_code == 0

        # Second run: no CLI settings -> both read from the cache.
        exit_code, _ = run_script(script, build_dir)
        assert exit_code == 0

    def test_failed_configure_does_not_persist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A build script that fails must not poison the cache."""
        from pcons.core.cache import CACHE_FILE

        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("raise RuntimeError('boom')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        exit_code, _ = run_script(script, build_dir, variables={"MY_VAR": "42"})
        assert exit_code == 1
        assert not (build_dir / CACHE_FILE).exists()

    def test_fresh_discards_persisted_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--fresh drops stale cached vars, keeping only this run's own."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        # First run persists HELLO.
        assert run_script(script, build_dir, variables={"HELLO": "1"})[0] == 0
        assert self._persisted_var(build_dir, "HELLO") == "1"

        # A --fresh run with a different var must not carry HELLO forward.
        assert (
            run_script(script, build_dir, variables={"WORLD": "2"}, fresh=True)[0] == 0
        )
        assert self._persisted_var(build_dir, "HELLO") is None
        assert self._persisted_var(build_dir, "WORLD") == "2"

    def test_fresh_ignores_cached_value_for_this_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A --fresh run reads the default, not a previously cached value."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("MY_VAR", raising=False)
        _clear_cli_vars()

        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, variables={"MY_VAR": "42"})[0] == 0

        # --fresh with no CLI var -> get_var sees the default, not cached 42.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "assert pcons.get_var('MY_VAR', 'default') == 'default'\n"
            "Project('demo')\n"
        )
        assert run_script(script, build_dir, fresh=True)[0] == 0

    def test_regen_run_does_not_persist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """persist=False (a regen re-invoke) writes no cache into the build dir."""
        from pcons.core.cache import CACHE_FILE

        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        exit_code, _ = run_script(
            script, build_dir, variables={"X": "1"}, variant="debug", persist=False
        )
        assert exit_code == 0
        assert not (build_dir / CACHE_FILE).exists()

    def test_regen_command_carries_no_cache_flag(self, tmp_path: Path) -> None:
        """The self-regeneration argv ends with --no-cache so it never persists."""
        from pcons.core.invocation import Invocation

        (tmp_path / "pcons-build.py").write_text("from pcons import Project\n")
        inv = Invocation(script=Path("pcons-build.py"), variant="release")
        argv = inv.command(root_dir=tmp_path, run_dir=tmp_path / "build")

        assert argv is not None
        assert "--no-cache" in argv

    def _persisted_generator(self, build_dir: Path) -> str | None:
        import json

        from pcons.core.cache import CACHE_FILE

        cache_file = build_dir / CACHE_FILE
        if not cache_file.exists():
            return None
        return json.loads(cache_file.read_text()).get("generator")

    def test_aux_generator_keeps_cached_build_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An aux-only -G metadata run keeps the cached build generator (sticky)."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.delenv("GENERATOR", raising=False)
        _clear_cli_vars()

        # Build generator persisted.
        assert run_script(script, build_dir, generator="make")[0] == 0
        assert self._persisted_generator(build_dir) == "make"

        # Aux-only run: build slot stays make, metadata added (not erased).
        assert run_script(script, build_dir, generator="metadata")[0] == 0
        assert self._persisted_generator(build_dir) == "make:metadata"

    def test_build_generator_replaces_cached_build_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new build generator replaces the cached one; aux from the new spec."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("PCONS_GENERATOR", raising=False)
        monkeypatch.delenv("GENERATOR", raising=False)
        _clear_cli_vars()

        assert run_script(script, build_dir, generator=["ninja", "metadata"])[0] == 0
        assert self._persisted_generator(build_dir) == "ninja:metadata"

        # make replaces ninja in the build slot; new spec has no aux.
        assert run_script(script, build_dir, generator="make")[0] == 0
        assert self._persisted_generator(build_dir) == "make"


class TestDirectoryArg:
    """Tests for -C/--directory argument.

    -C chdirs for real and CliRunner does not undo it, so each test that
    lands somewhere new fences the invocation with monkeypatch.chdir, which
    restores the original cwd at teardown whatever the CLI did in between.
    """

    def test_dash_c_changes_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that -C changes to the specified directory."""
        # Create a pcons-build.py in a subdirectory
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / "pcons-build.py").write_text('"""Test project."""\nprint("ok")\n')

        # Run pcons from tmp_path with -C myproject
        monkeypatch.chdir(tmp_path)
        result = _invoke("-C", str(subdir), "info")
        assert result.exit_code == 0
        assert "Test project" in result.stdout

    def test_long_form_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --directory=DIR form."""
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / "pcons-build.py").write_text('"""Long form test."""\nprint("ok")\n')

        monkeypatch.chdir(tmp_path)
        result = _invoke(f"--directory={subdir}", "info")
        assert result.exit_code == 0
        assert "Long form test" in result.stdout

    def test_dash_c_invalid_directory(self, tmp_path: Path) -> None:
        """Test -C with non-existent directory."""
        result = _invoke("-C", str(tmp_path / "nope"), "info")
        assert result.exit_code != 0
        assert "error" in result.stderr

    def test_dash_c_missing_arg(self) -> None:
        """Test -C without a directory argument."""
        result = _invoke("-C")
        assert result.exit_code != 0
        assert "requires an argument" in result.stderr

    def test_dash_c_init(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test -C works with init command."""
        subdir = tmp_path / "newproject"
        subdir.mkdir()

        monkeypatch.chdir(tmp_path)
        result = _invoke("-C", str(subdir), "init")
        assert result.exit_code == 0
        assert (subdir / "pcons-build.py").exists()
        # Should NOT exist in the original directory
        assert not (tmp_path / "pcons-build.py").exists()


class TestCLICommands:
    """Tests for CLI commands."""

    def test_pcons_help(self) -> None:
        """Test pcons --help."""
        result = _invoke("--help")
        assert result.exit_code == 0
        assert "pcons" in result.stdout
        assert "generate" in result.stdout
        assert "build" in result.stdout
        assert "clean" in result.stdout
        assert "init" in result.stdout

    def test_pcons_version(self) -> None:
        """Test pcons --version."""
        result = _invoke("--version")
        assert result.exit_code == 0
        # Check version is present (don't hardcode specific version)
        import pcons

        assert pcons.__version__ in result.stdout

    def test_pcons_init(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test pcons init in an empty dir scaffolds a working starter."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("init")
        assert result.exit_code == 0
        assert (tmp_path / "pcons-build.py").exists()
        # Empty dir: a hello-world C++ starter source is created
        assert (tmp_path / "src" / "main.cpp").exists()

        # Check content uses the canonical pcons API
        build_content = (tmp_path / "pcons-build.py").read_text()
        assert "from pcons import Project" in build_content
        assert 'toolchain="c++"' in build_content
        # No explicit generate call needed: generation is automatic
        assert ".generate(" not in build_content
        # PEP 723 metadata so `uv run pcons-build.py` works standalone
        assert "# /// script" in build_content
        assert '"pcons>=' in build_content
        # Project and program named after the directory
        assert f'Project("{tmp_path.name}")' in build_content
        assert '"src/main.cpp",' in build_content
        # Should NOT use internal imports or legacy boilerplate
        assert "NinjaGenerator" not in build_content
        assert "Generator()" not in build_content
        assert "from pcons.core" not in build_content
        assert "from pcons.generators" not in build_content

    def test_pcons_init_adopts_swift_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A directory of .swift sources gets toolchain="swift"."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.swift").write_text('print("hi")\n')

        monkeypatch.chdir(tmp_path)
        result = _invoke("init")
        assert result.exit_code == 0
        build_content = (tmp_path / "pcons-build.py").read_text()
        assert 'toolchain="swift"' in build_content
        assert '"src/main.swift",' in build_content
        # No starter source scaffolded over existing code
        assert not (tmp_path / "src" / "main.cpp").exists()

    def test_pcons_init_lang_c(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons init --lang c scaffolds a C starter."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("init", "--lang", "c")
        assert result.exit_code == 0
        assert (tmp_path / "src" / "main.c").exists()
        assert '"src/main.c",' in (tmp_path / "pcons-build.py").read_text()

    def test_pcons_init_adopts_existing_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons init generates a target from existing sources."""
        (tmp_path / "src" / "util").mkdir(parents=True)
        (tmp_path / "include").mkdir()
        (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
        (tmp_path / "src" / "util" / "helper.cpp").write_text("void helper() {}\n")
        (tmp_path / "include" / "helper.h").write_text("void helper();\n")

        monkeypatch.chdir(tmp_path)
        result = _invoke("init")
        assert result.exit_code == 0
        # No starter source is scaffolded over existing code
        assert not (tmp_path / "src" / "main.c").exists()

        build_content = (tmp_path / "pcons-build.py").read_text()
        assert '"src/main.cpp",' in build_content
        assert '"src/util/helper.cpp",' in build_content
        assert 'include_dirs.append("include")' in build_content

    def test_pcons_init_creates_valid_python(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that init creates syntactically valid Python."""
        monkeypatch.chdir(tmp_path)
        assert _invoke("init").exit_code == 0

        # Verify it's valid Python by compiling it
        build_py = tmp_path / "pcons-build.py"
        compile(build_py.read_text(), str(build_py), "exec")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows doesn't have Unix-style executable permissions",
    )
    def test_pcons_init_creates_executable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that init creates an executable file."""
        import stat

        monkeypatch.chdir(tmp_path)
        assert _invoke("init").exit_code == 0

        build_py = tmp_path / "pcons-build.py"
        mode = build_py.stat().st_mode
        assert mode & stat.S_IXUSR, "pcons-build.py should be executable"

    def test_pcons_init_template_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that the init template can actually run and generate ninja."""
        # Skip if no C compiler available
        if not _has_c_compiler():
            pytest.skip("no C compiler found")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("init").exit_code == 0

        # Run the generated pcons-build.py via pcons generate
        result = _invoke("generate")
        assert result.exit_code == 0, f"generate failed: {result.output}"
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_auto_generate_without_generate_call(self, tmp_path: Path) -> None:
        """A script with no generate call auto-generates, even run directly."""
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\nproject = Project('auto')\n"
        )
        # Subprocess: the generation this pins happens in an atexit hook, which
        # only runs when the interpreter itself exits, and the script is run
        # directly rather than through the CLI.
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
        # Subprocess: same atexit path as the test above, and the traceback it
        # asserts on is written by the interpreter, not by pcons.
        result = subprocess.run(
            [sys.executable, "pcons-build.py"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "boom" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_no_auto_generate_on_sys_exit_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A script that sys.exit()s nonzero under the CLI must not generate."""
        (tmp_path / "pcons-build.py").write_text(
            "import sys\n"
            "from pcons import Project\n"
            "project = Project('bail')\n"
            "sys.exit(3)\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("generate").exit_code == 3
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_pcons_init_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons init --force overwrites files."""
        # Create existing file
        (tmp_path / "pcons-build.py").write_text("# old content")
        monkeypatch.chdir(tmp_path)

        # Without --force should fail
        assert _invoke("init").exit_code != 0

        # With --force should succeed
        assert _invoke("init", "--force").exit_code == 0

        # Check content was replaced
        build_content = (tmp_path / "pcons-build.py").read_text()
        assert "from pcons import Project" in build_content
        assert 'toolchain="c++"' in build_content

    def test_pcons_info(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test pcons info shows pcons-build.py docstring."""
        # Create a pcons-build.py with a docstring
        build_py = tmp_path / "pcons-build.py"
        build_py.write_text('''"""My project build script.

Variables:
    FOO - Some variable (default: bar)
"""
print("hello")
''')

        monkeypatch.chdir(tmp_path)
        result = _invoke("info")
        assert result.exit_code == 0
        assert "My project build script" in result.stdout
        assert "FOO" in result.stdout

    def test_pcons_info_no_docstring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons info handles missing docstring gracefully."""
        build_py = tmp_path / "pcons-build.py"
        build_py.write_text('print("hello")\n')

        monkeypatch.chdir(tmp_path)
        result = _invoke("info")
        assert result.exit_code == 0
        assert "No docstring found" in result.stdout

    def test_pcons_info_no_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons info without pcons-build.py."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("info")
        assert result.exit_code != 0
        assert "No pcons-build.py found" in result.stderr

    def test_pcons_generate_no_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons generate without pcons-build.py."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("generate")
        assert result.exit_code != 0
        assert "No pcons-build.py found" in result.stderr

    def test_pcons_build_no_build_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons build without any build files (ninja, make, or xcode)."""
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("build")
        assert result.exit_code != 0
        assert "No build files found" in result.stderr

    def test_main_entry_point_propagates_exit_code(self, tmp_path: Path) -> None:
        """__main__.py must call sys.exit(main()) so build failures propagate."""
        # Subprocess: the assertion is about pcons/__main__.py wiring
        # sys.exit(main()), which only a real process exit code shows.
        result = subprocess.run(
            [sys.executable, "-m", "pcons", "build"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode != 0

    def test_pcons_clean_no_ninja(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons clean without build.ninja (should succeed)."""
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        # Clean with no build.ninja should succeed (nothing to clean)
        assert _invoke("clean").exit_code == 0

    def test_pcons_clean_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons clean --all removes build directory."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "hello.o").write_text("# fake object file")

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("clean", "--all").exit_code == 0
        assert not build_dir.exists()


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing edge cases.

    These tests ensure that KEY=value arguments are not mistaken for commands.
    """

    def test_variable_without_command_no_build_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that VAR=value without a command doesn't error on argument parsing.

        Without pcons-build.py it should fail gracefully, not with 'invalid choice'.
        """
        monkeypatch.chdir(tmp_path)
        result = _invoke("FOO=bar")
        # Should fail because no pcons-build.py, not because of argument parsing
        assert result.exit_code != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_variable_with_build_dir_option(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test -B option with variable doesn't confuse argument parsing."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("-B", "mybuild", "VAR=value")
        # Should fail because no pcons-build.py, not because of argument parsing
        assert result.exit_code != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_multiple_variables_without_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test multiple KEY=value args without a command."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("FOO=1", "BAR=2", "BAZ=3")
        assert result.exit_code != 0
        assert "No pcons-build.py found" in result.stderr
        assert "invalid choice" not in result.stderr

    def test_help_shows_commands(self) -> None:
        """Test that --help shows available commands."""
        result = _invoke("--help")
        assert result.exit_code == 0
        # Should show available commands
        assert "info" in result.stdout
        assert "init" in result.stdout
        assert "generate" in result.stdout
        assert "build" in result.stdout
        assert "clean" in result.stdout

    def test_subcommand_help(self) -> None:
        """Test that subcommand --help works."""
        result = _invoke("build", "--help")
        assert result.exit_code == 0
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
        result = _invoke("test", "--manifest", str(manifest), "--list", "--no-color")
        assert result.exit_code == 0
        assert "demo" in result.stdout

    def test_test_dispatch_not_confused_by_option_value(self, tmp_path: Path) -> None:
        """An option VALUE equal to 'test' must not be mistaken for the subcommand.

        `pcons --build-dir test test ...` has "test" appearing twice: once
        as the value of --build-dir, once as the actual subcommand. Locating
        the dispatch point by scanning raw argv for the literal string
        "test" (sys.argv.index("test")) finds the option value first and
        hands the runner a bogus leading "test" positional, which its
        argparse rejects. The option's value must be consumed as a value
        before the first remaining token is read as the command.
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
        result = _invoke(
            "--build-dir",
            "test",
            "test",
            "--manifest",
            str(manifest),
            "--list",
            "--no-color",
        )
        assert result.exit_code == 0, result.output
        assert "demo" in result.stdout

    def test_generate_with_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test pcons generate VAR=value works."""
        # Create a minimal pcons-build.py that just prints the variable
        build_py = tmp_path / "pcons-build.py"
        build_py.write_text("""\
import os
from pcons import get_var
print(f"TEST_VAR={get_var('TEST_VAR', 'not_set')}")
""")

        monkeypatch.chdir(tmp_path)
        result = _invoke("generate", "TEST_VAR=myvalue")
        # The script will fail (no ninja generation) but should have received the var
        assert "TEST_VAR=myvalue" in result.stdout

    def test_options_before_and_after_command(self) -> None:
        """Test that options work both before and after command."""
        # Options before command
        result = _invoke("-v", "build", "--help")
        assert result.exit_code == 0
        assert "targets" in result.stdout

    def test_info_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("info", "--targets")
        assert result.exit_code == 0
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

        # Subprocess for the whole cycle: this test compiles and links with a
        # real toolchain, runs real ninja and then executes the binary, so the
        # thing under test is the tools pcons drives, not pcons' own parsing.
        # Run generate (which includes configuration)
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "generate"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0, f"generate failed: {result.stderr}"
        assert (tmp_path / "build" / "build.ninja").exists()

        # Run build (subprocess: invokes real ninja and a real compiler)
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

        # Run the built program (subprocess: it is a compiled binary, not pcons)
        hello_path = tmp_path / "build" / "hello"
        if not hello_path.exists():
            hello_path = tmp_path / "build" / "hello.exe"

        result = subprocess.run([str(hello_path)], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Hello, pcons!" in result.stdout

        # Run clean (subprocess: last step of the same end-to-end sequence)
        result = subprocess.run(
            [sys.executable, "-m", "pcons.cli", "clean", "--all"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert not (tmp_path / "build").exists()


class TestUnreadCachedVarWarning:
    """The CLI warns about persisted vars the build script never reads (typos)."""

    def _run(
        self,
        script: Path,
        build_dir: Path,
        caplog,
        **kwargs,
    ) -> list[str]:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pcons"):
            assert run_script(script, build_dir, **kwargs)[0] == 0
        return [r.message for r in caplog.records]

    def test_warns_on_typo_cached_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        # Persist a typo'd var and a real one.
        script.write_text("from pcons import Project\nProject('demo')\n")
        assert (
            run_script(script, build_dir, variables={"FEATRUE": "on", "FEATURE": "on"})[
                0
            ]
            == 0
        )

        # A later bare run whose script reads only FEATURE.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "pcons.get_var('FEATURE')\n"
            "Project('demo')\n"
        )
        msgs = self._run(script, build_dir, caplog)
        assert any("FEATRUE" in m for m in msgs)
        assert not any("'FEATURE'" in m for m in msgs)

    def test_no_warning_when_all_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, variables={"PORT": "8080"})[0] == 0

        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "pcons.get_var('PORT')\n"
            "Project('demo')\n"
        )
        msgs = self._run(script, build_dir, caplog)
        assert not any("PORT" in m for m in msgs)

    def test_no_warning_for_var_set_this_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """A var set fresh on this run's command line is not nagged, even unread."""
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        msgs = self._run(script, build_dir, caplog, variables={"NEWVAR": "1"})
        assert not any("NEWVAR" in m for m in msgs)


class TestSourceDirMismatch:
    """The cache records its source dir and refuses to apply to another tree."""

    def _script(self, dir_: Path, body: str) -> Path:
        dir_.mkdir(parents=True, exist_ok=True)
        script = dir_ / "pcons-build.py"
        script.write_text(body)
        return script

    def test_records_source_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.core.cache import BuildCache

        build_dir = tmp_path / "build"
        src = self._script(
            tmp_path / "a", "from pcons import Project\nProject('demo')\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        assert run_script(src, build_dir)[0] == 0
        assert BuildCache(build_dir).get("source_dir") == str(src.parent)

    def test_moved_cache_is_ignored_with_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        build_dir = tmp_path / "build"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("HELLO", raising=False)
        _clear_cli_vars()

        # Configure in source tree A.
        src_a = self._script(
            tmp_path / "a", "from pcons import Project\nProject('demo')\n"
        )
        assert run_script(src_a, build_dir, variables={"HELLO": "1"})[0] == 0

        # Simulate a separate process: a real second `pcons` run starts with a
        # clean project tree. (run_script doesn't reset it; the CLI process does.)
        from pcons.core.project import Project

        Project._clear_tree()

        # A script in tree B, same build dir, must not inherit A's HELLO.
        src_b = self._script(
            tmp_path / "b",
            "import pcons\n"
            "from pcons import Project\n"
            "assert pcons.get_var('HELLO', 'def') == 'def'\n"
            "Project('demo')\n",
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="pcons"):
            assert run_script(src_b, build_dir)[0] == 0
        assert any("source dir" in r.message for r in caplog.records)

        # The cache now belongs to B.
        from pcons.core.cache import BuildCache

        assert BuildCache(build_dir).get("source_dir") == str(src_b.parent)


class TestEnvOverridesCache:
    """An exported PCONS_* env var overrides the persisted cache (but not a CLI
    flag), and is itself never persisted."""

    def _persisted(self, build_dir: Path, key: str) -> object:
        from pcons.core.cache import BuildCache

        return BuildCache(build_dir).get(key)

    def test_pcons_variant_env_overrides_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        # Persist variant=release.
        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, variant="release")[0] == 0

        # An exported PCONS_VARIANT beats the cached release.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "assert pcons.get_variant() == 'debug', pcons.get_variant()\n"
            "Project('demo')\n"
        )
        monkeypatch.setenv("PCONS_VARIANT", "debug")
        assert run_script(script, build_dir)[0] == 0
        # But the env value did not rewrite the cache.
        assert self._persisted(build_dir, "variant") == "release"

    def test_cli_variant_beats_pcons_variant_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "assert pcons.get_variant() == 'release', pcons.get_variant()\n"
            "Project('demo')\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.setenv("PCONS_VARIANT", "debug")
        _clear_cli_vars()

        # The --variant flag wins over the exported PCONS_VARIANT.
        assert run_script(script, build_dir, variant="release")[0] == 0

    def test_pcons_generator_env_overrides_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, generator="ninja")[0] == 0

        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "assert isinstance(pcons.Generator(), pcons.MakefileGenerator)\n"
            "Project('demo')\n"
        )
        monkeypatch.setenv("PCONS_GENERATOR", "make")
        assert run_script(script, build_dir)[0] == 0
        assert self._persisted(build_dir, "generator") == "ninja"

    def test_pcons_vars_env_overrides_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("PORT", raising=False)
        _clear_cli_vars()

        # Persist PORT=1.
        script.write_text("from pcons import Project\nProject('demo')\n")
        assert run_script(script, build_dir, variables={"PORT": "1"})[0] == 0

        # An exported PCONS_VARS overrides the cached PORT.
        script.write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "assert pcons.get_var('PORT') == '2', pcons.get_var('PORT')\n"
            "Project('demo')\n"
        )
        monkeypatch.setenv("PCONS_VARS", '{"PORT": "2"}')
        assert run_script(script, build_dir)[0] == 0
        # The env value did not rewrite the cached PORT.
        assert self._persisted(build_dir, "vars") == {"PORT": "1"}


class TestCacheCommand:
    """Tests for the `pcons cache` subcommand (list/show/clear/path)."""

    def _populate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        build_dir = tmp_path / "build"
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\nProject('demo')\n")
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()
        run_script(
            script,
            build_dir,
            variables={"HELLO": "42"},
            variant="debug",
            generator="ninja",
        )
        return build_dir

    def _args(self, build_dir: Path, action: str) -> argparse.Namespace:
        return argparse.Namespace(build_dir=str(build_dir), cache_action=action)

    def test_cache_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        build_dir = self._populate(tmp_path, monkeypatch)
        assert cmd_cache(self._args(build_dir, "list")) == 0
        out = capsys.readouterr().out
        assert "HELLO=42" in out
        assert "variant=debug" in out
        assert "generator=ninja" in out

    def test_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from pcons.core.cache import CACHE_FILE

        build_dir = self._populate(tmp_path, monkeypatch)
        assert cmd_cache(self._args(build_dir, "path")) == 0
        assert str(build_dir / CACHE_FILE) in capsys.readouterr().out

    def test_cache_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        build_dir = self._populate(tmp_path, monkeypatch)
        assert cmd_cache(self._args(build_dir, "clear")) == 0
        capsys.readouterr()
        # After clearing, list shows no settings.
        assert cmd_cache(self._args(build_dir, "list")) == 0
        assert capsys.readouterr().out.strip() == ""

    def test_cache_missing_reports_cleanly(self, tmp_path: Path, capsys) -> None:
        assert cmd_cache(self._args(tmp_path / "nope", "list")) == 0
        assert "No cache" in capsys.readouterr().out


class TestCacheCLI:
    """The cache outlives the process that wrote it.

    Every other cache test runs one `pcons` in this interpreter, where the
    project registry and the vars cache are module state that a second
    in-process run inherits. These assert that a value configured by one
    invocation is read back by the *next* one, which only separate processes
    can show.
    """

    def _script(self, tmp_path: Path) -> None:
        (tmp_path / "pcons-build.py").write_text(
            "import pcons\n"
            "from pcons import Project\n"
            "pcons.get_var('HELLO')\n"
            "Project('demo')\n"
        )

    def _run(self, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        # Subprocess: a fresh interpreter per run is the point, see the class
        # docstring. In-process these would share the caches they are meant to
        # prove were persisted to disk and re-read.
        return subprocess.run(
            [sys.executable, "-m", "pcons.cli", *args],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    def test_var_persists_across_cli_runs(self, tmp_path: Path) -> None:
        self._script(tmp_path)
        r = self._run(tmp_path, "generate", "HELLO=42")
        assert r.returncode == 0, r.stderr
        r = self._run(tmp_path, "cache", "list")
        assert r.returncode == 0, r.stderr
        assert "HELLO=42" in r.stdout

    def test_cache_clear_via_cli(self, tmp_path: Path) -> None:
        self._script(tmp_path)
        assert self._run(tmp_path, "generate", "HELLO=42").returncode == 0
        assert self._run(tmp_path, "cache", "clear").returncode == 0
        r = self._run(tmp_path, "cache", "list")
        assert "HELLO=42" not in r.stdout

    def test_fresh_flag_via_cli(self, tmp_path: Path) -> None:
        self._script(tmp_path)
        assert self._run(tmp_path, "generate", "HELLO=1").returncode == 0
        assert self._run(tmp_path, "generate", "--fresh", "WORLD=2").returncode == 0
        r = self._run(tmp_path, "cache", "list")
        assert "HELLO" not in r.stdout
        assert "WORLD=2" in r.stdout


class TestGlobalOptionsBeforeTheCommand:
    """An option spelled before the subcommand must survive it.

    argparse applied a subparser's defaults on top of what the top-level
    parser had already stored, so `pcons -B out generate` used to fall back
    to `build/` without a word.
    """

    def test_build_dir_before_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("-B", "out", "generate").exit_code == 0
        assert seen[0].build_dir == "out"

    def test_build_dir_after_the_command_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("-B", "out", "generate", "-B", "late").exit_code == 0
        assert seen[0].build_dir == "late"

    def test_build_dir_defaults_when_given_nowhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("generate").exit_code == 0
        assert seen[0].build_dir == "build"

    def test_build_dir_from_the_environment_loses_to_the_command_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The subcommand's own value comes from the environment, not the
        command line, so it must not beat a -B spelled before the command."""
        monkeypatch.setenv("PCONS_BUILD_DIR", "from-env")
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("-B", "out", "generate").exit_code == 0
        assert seen[0].build_dir == "out"

    def test_verbose_before_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("-v", "generate").exit_code == 0
        assert seen[0].verbose is True

    def test_variant_before_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _capture_command(monkeypatch, "cmd_build")
        assert _invoke("--variant", "release", "build").exit_code == 0
        assert seen[0].variant == "release"

    def test_command_only_options_are_unaffected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, "cmd_clean")
        assert _invoke("clean", "--all").exit_code == 0
        assert seen[0].all is True
        assert _invoke("clean").exit_code == 0
        assert seen[1].all is False


class TestCommandDetection:
    """The subcommand must be found whatever precedes it.

    Locating it used to mean scanning argv against a hand-written table of
    every value-taking option in this CLI and in the test runner, so an
    option missing from the table turned the next token into the command.
    click parses against each command's own declarations, so there is no
    table left to keep complete.
    """

    def test_generator_before_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # -G was missing from that table, so `make` read as the first
        # positional, `generate` became a build target, and pcons generated
        # and then asked the build tool for a target named "generate".
        ran_default = _capture_command(monkeypatch, "_run_default")
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("-G", "make", "generate").exit_code == 0
        assert not ran_default
        assert seen[0].command == "generate"
        assert seen[0].generator == ["make"]

    def test_long_generator_before_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, "_run_default")
        seen = _capture_command(monkeypatch, "_cmd_generate_wrapper")
        assert _invoke("--generator", "make", "generate").exit_code == 0
        assert not ran_default
        assert seen[0].command == "generate"

    def test_option_value_that_names_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, "cmd_build")
        assert _invoke("-G", "make", "-B", "test", "build").exit_code == 0
        assert seen[0].command == "build"
        assert seen[0].build_dir == "test"


class TestDirectoryOption:
    """-C DIR chdirs before anything else, on either side of the command."""

    def test_missing_directory_before_the_command(self, tmp_path: Path) -> None:
        result = _invoke("-C", str(tmp_path / "nope"), "generate")
        assert result.exit_code == 1
        assert "error: -C" in result.output

    def test_missing_directory_after_the_command(self, tmp_path: Path) -> None:
        result = _invoke("generate", "-C", str(tmp_path / "nope"))
        assert result.exit_code == 1
        assert "error: -C" in result.output


class TestBuildDirArgs:
    """`pcons test` owns its parser, so the CLI hands it the build dir."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["-B", "out"],
            ["--build-dir", "out"],
            ["--build-dir=out"],
            ["-Bout"],
            ["-v", "-B", "out"],
        ],
    )
    def test_every_spelling_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        seen = _capture_test_runner(monkeypatch)
        assert _invoke(*argv, "test").exit_code == 0
        assert seen == [["-B", "out"]]

    def test_nothing_to_forward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no -B the runner searches upward for the manifest itself, so
        forwarding a default build directory would silently stop the search."""
        monkeypatch.setenv("PCONS_BUILD_DIR", "from-env")
        seen = _capture_test_runner(monkeypatch)
        assert _invoke("test", "--list").exit_code == 0
        assert seen == [["--list"]]

    def test_trailing_option_without_a_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_test_runner(monkeypatch)
        assert _invoke("-B").exit_code == 2
        assert seen == []

    def test_main_hands_the_runner_the_build_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[list[str]] = []

        def fake_runner(argv: list[str]) -> int:
            seen.append(argv)
            return 0

        monkeypatch.setattr("pcons.test_runner.main", fake_runner)
        monkeypatch.setattr(sys, "argv", ["pcons", "-B", "out", "test", "-j", "1"])
        assert cli_main() == 0
        assert seen == [["-B", "out", "-j", "1"]]


def test_windows_argv_expansion_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """click expands ~, $VAR, %VAR% and globs in argv on Windows unless told not to.

    This asserts the keyword rather than the behaviour: CliRunner always passes
    an explicit argv, so the expansion is unreachable from a test, and it is
    Windows-only besides. Asserting the keyword is what fails on any platform
    when someone deletes it.
    """
    seen: dict[str, object] = {}

    def fake_main(**kw: object) -> int:
        seen.update(kw)
        return 0

    monkeypatch.setattr("pcons.cli.cli.main", fake_main)
    assert cli_main() == 0
    assert seen["windows_expand_args"] is False
