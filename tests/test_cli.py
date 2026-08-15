# SPDX-License-Identifier: MIT
"""Tests for pcons CLI."""

from __future__ import annotations

import inspect
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner, Result

import pcons.cli as cli_module
from pcons import (
    Generator,
    MakefileGenerator,
    MetadataGenerator,
    MultiGenerator,
    NinjaGenerator,
)
from pcons._cli_click import MergingCommand, MergingGroup, PconsGroup
from pcons.cli import (
    _cache_clear,
    _cache_list,
    _cache_path,
    _cache_show,
    _clean,
    _find_ninja,
    _load_user_modules,
    _needs_generation,
    _parse_pcons_vars,
    cli,
    cli_build,
    cli_cache_path,
    cli_clean,
    cli_default,
    cli_generate,
    cli_info,
    cli_init,
    find_script,
    parse_variables,
    run_make,
    run_ninja,
    run_script,
    run_xcodebuild,
    setup_logging,
)
from pcons.cli import (
    main as cli_main,
)
from pcons.core.vars import _clear_cli_vars
from tests.support import subprocess_env


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
    monkeypatch: pytest.MonkeyPatch, command: click.Command
) -> list[dict[str, object]]:
    """Stand in for a command's body so a test can read what the parser gave it.

    click calls a command's callback with its parameters as keyword arguments,
    so what lands here is exactly what the command would have worked from.
    """
    seen: list[dict[str, object]] = []

    def fake(**kw: object) -> int:
        seen.append(kw)
        return 0

    monkeypatch.setattr(command, "callback", fake)
    return seen


def _capture_args(
    monkeypatch: pytest.MonkeyPatch, name: str, result: object = 0
) -> list[dict[str, object]]:
    """Stand in for a work function so a test can read the arguments it got.

    Arguments are bound through the real signature, so a value passed
    positionally is recorded under its parameter's name. Asserting on
    ``seen[0]["build_dir"]`` would otherwise pass or fail on how the caller
    chose to spell the call rather than on what it sent.
    """
    real = getattr(cli_module, name)
    seen: list[dict[str, object]] = []

    def fake(*args: object, **kw: object) -> object:
        bound = inspect.signature(real).bind(*args, **kw)
        bound.apply_defaults()
        seen.append(dict(bound.arguments))
        return result

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


def _main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run the CLI through main(), the way the console script does.

    CliRunner drives cli.main(standalone_mode=True), so click's own standalone
    handler produces every exit code the rest of this file observes. main()
    passes standalone_mode=False and handles the outcome itself, so its
    exception handling, its prog_name and its return-value bridge only run
    here.

    The logging handlers are saved and restored for the reason given on
    _invoke, capsys being the buffer that would otherwise be captured.
    """
    handlers = logging.root.handlers[:]
    level = logging.root.level
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sys, "argv", ["pcons", *argv])
            code = cli_main()
    finally:
        logging.root.handlers[:] = handlers
        logging.root.setLevel(level)
    out, err = capsys.readouterr()
    return code, out, err


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

    def test_reconfigure_reaches_the_script_as_an_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PCONS_RECONFIGURE is the whole of --reconfigure. Drop the one line
        that sets it and the flag parses, the run succeeds, and the cached
        configuration is reused anyway."""
        script = tmp_path / "pcons-build.py"
        script.write_text(
            "import os\n"
            "from pcons import Project\n"
            "assert os.environ['PCONS_RECONFIGURE'] == '1'\n"
            "Project('demo')\n"
        )

        monkeypatch.delenv("PCONS_RECONFIGURE", raising=False)
        _clear_cli_vars()

        exit_code, _ = run_script(script, tmp_path / "build", reconfigure=True)
        assert exit_code == 0

    def test_a_pcons_error_is_reported_without_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """A PconsError carries an actionable message a traceback would bury,
        so it gets its own arm. It must also cancel the pending generation:
        build files written from a half-run script are worse than none."""
        script = tmp_path / "pcons-build.py"
        script.write_text(
            "from pcons import Project\n"
            "from pcons.core.errors import PconsError\n"
            "Project('demo')\n"
            "raise PconsError('no toolchain for wombat')\n"
        )

        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()

        with caplog.at_level(logging.ERROR, logger="pcons"):
            exit_code, projects = run_script(script, tmp_path / "build")

        assert exit_code == 1
        assert projects == []
        assert "no toolchain for wombat" in caplog.text
        assert "Traceback" not in caplog.text
        assert not (tmp_path / "build" / "build.ninja").exists()

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
        """-C with no directory is a usage error, so it exits 2.

        A -C naming a directory that does not exist is a different thing and
        still exits 1, pinned by TestDirectoryOption. Every other option that
        misses its value exits 2, and -C used to be the exception.
        """
        result = _invoke("-C")
        assert result.exit_code == 2
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
        # A build script is run by pcons, so it carries neither a PEP 723
        # header nor a shebang: nothing runs the file itself.
        assert "# /// script" not in build_content
        assert "#!" not in build_content
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

    def test_no_auto_generate_on_script_crash(self, tmp_path: Path) -> None:
        """A crashed script must not generate build files at exit."""
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\n"
            "project = Project('crash')\n"
            "raise RuntimeError('boom')\n"
        )
        # Subprocess: the traceback it asserts on is written by the
        # interpreter, not by pcons.
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

    def test_sys_exit_message_reaches_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """raise SystemExit("msg") must print the message, like python does."""
        (tmp_path / "pcons-build.py").write_text(
            "from pcons import Project\n"
            "project = Project('bail')\n"
            "raise SystemExit('no sources found for test_js')\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("generate")
        assert result.exit_code == 1
        assert "no sources found for test_js" in result.output
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_sys_exit_zero_still_generates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sys.exit(0) ends the script successfully; a direct run would still
        generate via atexit, so the CLI must do the same (and must not leave
        the pending generation to fire at interpreter shutdown)."""
        (tmp_path / "pcons-build.py").write_text(
            "import sys\n"
            "from pcons import Project\n"
            "project = Project('early')\n"
            "sys.exit(0)\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("generate").exit_code == 0
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_sys_exit_zero_before_any_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sys.exit(0) before a Project exists is a clean, quiet exit."""
        (tmp_path / "pcons-build.py").write_text("import sys\nsys.exit(0)\n")
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("generate")
        assert result.exit_code == 0
        assert "No Project" not in result.output
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_sys_exit_zero_under_explain_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-generating command (explain) honors sys.exit(0) without
        writing build files."""
        (tmp_path / "pcons-build.py").write_text(
            "import sys\n"
            "from pcons import Project\n"
            "project = Project('early')\n"
            "sys.exit(0)\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("explain").exit_code == 0
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


class TestScriptThatDescribesNoBuild:
    """A script may exit 0 having created no project, and that is not a failure.

    An optional toolchain is missing, or the script is outside the environment
    it is meant to run in: it says so and stops. No project means nothing
    enqueued a generate, so there are no build files to run afterwards, and
    looking for them and reporting them missing turns the script's clean stop
    into an error it never signalled.
    """

    SKIP_SCRIPT = """\
import sys
from pathlib import Path

(Path(__file__).parent / "runs").open("a").write("x")
sys.exit(0)
"""

    def _write_script(self, tmp_path: Path) -> Path:
        (tmp_path / "pcons-build.py").write_text(self.SKIP_SCRIPT)
        return tmp_path / "runs"

    @pytest.mark.parametrize("argv", [(), ("build",), ("generate",)])
    def test_skipping_is_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...]
    ) -> None:
        self._write_script(tmp_path)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        result = _invoke(*argv)
        assert result.exit_code == 0
        assert "No build files found" not in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    @pytest.mark.parametrize("argv", [(), ("build",), ("generate",)])
    def test_the_reason_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...]
    ) -> None:
        # Without -v: a script that stops without a word of its own would
        # otherwise leave a build that did not happen as silence and a zero.
        self._write_script(tmp_path)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        assert "described no build" in _invoke(*argv).stderr

    def test_the_script_runs_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A bare `pcons` generates and then builds, and the build regenerates
        # when the build files are missing -- which they always are here.
        runs = self._write_script(tmp_path)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        assert _invoke().exit_code == 0
        assert runs.read_text() == "x"


class TestExplainCommand:
    """Tests for `pcons explain`."""

    COMMAND_SCRIPT = """\
from pcons import Project

project = Project("demo")
env = project.Environment()
env.Command(
    target="out.txt",
    source=["in.txt"],
    command=["copytool", "--from", "$SOURCE", "--to", "$TARGET"],
)
"""

    def _write_project(self, tmp_path: Path) -> None:
        (tmp_path / "in.txt").write_text("data\n")
        (tmp_path / "pcons-build.py").write_text(self.COMMAND_SCRIPT)

    def test_shows_concrete_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The resolved command is printed with real paths, markers expanded."""
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "## Explanation of Targets and Environments" in result.stdout
        assert "out.txt  <-  in.txt" in result.stdout
        # The command is spelled as it runs from the build directory.
        assert "copytool --from ../in.txt --to out.txt" in result.stdout

    def test_width_truncates_command_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--width cuts command lines, marking the cut with an ellipsis."""
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain", "--width", "20")
        assert result.exit_code == 0
        command_lines = [
            line for line in result.stdout.splitlines() if "copytool" in line
        ]
        assert command_lines
        assert all(len(line) <= 20 for line in command_lines)
        assert any(line.endswith("...") for line in command_lines)

    def test_color_always_emits_ansi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--color always styles the report even when piped."""
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert "\x1b[" not in _invoke("explain").stdout
        assert "\x1b[" in _invoke("explain", "--color", "always").stdout

    def test_writes_no_build_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """explain is an inspection command: no build.ninja, no cache entry."""
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert not (tmp_path / "build" / "build.ninja").exists()
        assert not (tmp_path / "build" / "pcons_cache.json").exists()

    def test_unknown_target_lists_the_real_ones(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain", "nosuch")
        assert result.exit_code == 1

    def test_named_target_filters_the_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Naming targets narrows the report to just those."""
        script = self.COMMAND_SCRIPT + (
            "env.Command(\n"
            '    target="other.txt",\n'
            '    source=["in.txt"],\n'
            '    command=["othertool", "$SOURCE", "$TARGET"],\n'
            ")\n"
        )
        (tmp_path / "in.txt").write_text("data\n")
        (tmp_path / "pcons-build.py").write_text(script)
        monkeypatch.chdir(tmp_path)

        all_result = _invoke("explain")
        assert all_result.exit_code == 0
        assert "othertool" in all_result.stdout

        first = next(
            line for line in all_result.stdout.splitlines() if "(command)" in line
        )
        # A target header reads `=== <name>  (<type>)  [env <label>]`.
        target_name = first.split()[1]
        one_result = _invoke("explain", target_name)
        assert one_result.exit_code == 0
        assert "copytool" in one_result.stdout
        assert "othertool" not in one_result.stdout

    def test_flag_provenance_names_the_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a real toolchain, flags are attributed to variant and preset."""
        if not _has_c_compiler():
            pytest.skip("no C compiler found")

        (tmp_path / "hello.c").write_text("int main(void) { return 0; }\n")
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("hello")
env = project.Environment(toolchain="c")
env.set_variant("debug")
env.apply_preset("warnings")
project.Program("hello", env, sources=["hello.c"])
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        # The commands section shows the real compile line...
        assert "hello.c" in result.stdout
        # ...and the provenance section attributes flags to their presets.
        assert "(variant)" in result.stdout
        assert "warnings (feature)" in result.stdout

    def test_requirements_attribute_their_contributor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Usage requirements name the target that contributed them."""
        if not _has_c_compiler():
            pytest.skip("no C compiler found")

        (tmp_path / "include").mkdir()
        (tmp_path / "include" / "lib.h").write_text("int lib(void);\n")
        (tmp_path / "lib.c").write_text("int lib(void) { return 1; }\n")
        (tmp_path / "main.c").write_text(
            '#include "lib.h"\nint main(void) { return lib(); }\n'
        )
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("demo")
env = project.Environment(toolchain="c")
mylib = project.StaticLibrary("mylib", env, sources=["lib.c"])
mylib.public.include_dirs.append("include")
mylib.public.defines.append("USE_MYLIB")
app = project.Program("app", env, sources=["main.c"])
app.link(mylib)
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain", "app")
        assert result.exit_code == 0
        assert "requirements:" in result.stdout
        assert "<- mylib (public)" in result.stdout
        assert "USE_MYLIB" in result.stdout

    def test_sibling_sources_collapse_to_braces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dependencies sharing a directory collapse shell-style."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "one.txt").write_text("1\n")
        (tmp_path / "src" / "two.txt").write_text("2\n")
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("demo")
env = project.Environment()
env.Command(
    target="out.txt",
    source=["src/one.txt", "src/two.txt"],
    command=["cat", "$SOURCES", "$TARGET"],
)
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "<-  src/{one.txt,two.txt}" in result.stdout
        # The command itself stays literal, spelled from the build dir.
        assert "cat ../src/one.txt ../src/two.txt out.txt" in result.stdout

    def test_multi_target_commands_show_each_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Indexed ${TARGETS[n]} tokens render the actual outputs, not the
        primary node repeated (env.Command stores them under all_targets)."""
        (tmp_path / "in.txt").write_text("data\n")
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("demo")
env = project.Environment()
env.Command(
    target=["a.out.txt", "b.out.txt"],
    source=["in.txt"],
    command=["gen", "--first", "${TARGETS[0]}", "--second", "${TARGETS[1]}", "$SOURCE"],
)
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "--first a.out.txt --second b.out.txt ../in.txt" in result.stdout

    def test_cwd_commands_show_the_cd_and_cwd_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An edge with cwd= is spelled from that directory, behind a cd,
        matching what the generators emit."""
        (tmp_path / "in.txt").write_text("data\n")
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("demo")
env = project.Environment()
env.Command(
    target="out.txt",
    source=["in.txt"],
    command=["tool", "$SOURCE", "$TARGET"],
    cwd=project.root_dir,
)
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "cd .. && tool in.txt" in result.stdout

    def test_install_targets_show_their_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env-less nodes (standalone install tool) render via the same
        fallback the generators use, not as an empty section."""
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "data.txt").write_text("data\n")
        (tmp_path / "pcons-build.py").write_text(
            """\
from pcons import Project

project = Project("demo")
project.InstallDir(".", "assets", name="install-assets")
"""
        )
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "install-assets" in result.stdout
        assert "copytree" in result.stdout

    def test_target_header_names_its_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each target header carries its environment's label."""
        self._write_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = _invoke("explain")
        assert result.exit_code == 0
        assert "[env #1]" in result.stdout
        assert "Environment #1" in result.stdout
        # Definition locations, relative to the project root.
        assert "pcons-build.py:" in result.stdout


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

    def test_a_target_reaches_the_build_as_a_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A target arrives as `extra`, the same argument KEY=value arrives in,
        and has to reach the build tool as a target rather than be read as a
        variable or as something to generate from.
        """
        (tmp_path / "pcons-build.py").write_text("from pcons import Project\n")
        monkeypatch.chdir(tmp_path)
        _capture_args(
            monkeypatch, "_generate", result=(0, SimpleNamespace(build_dir=tmp_path))
        )
        built = _capture_args(monkeypatch, "_build", result=(0, tmp_path))
        assert _invoke("hello").exit_code == 0
        assert built[0]["targets"] == ["hello"]

    def test_a_bare_dash_is_not_a_target(self) -> None:
        """A first token that looks like an option stays an error.

        Only an unresolvable command *name* falls through to the catch-all. A
        bare `-` is the one option-shaped token click's group parser hands on
        rather than rejecting itself, so it is what reaches that guard.
        """
        result = _invoke("-")
        assert result.exit_code == 2
        assert "No such command '-'." in result.stderr

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

    def test_value_options_name_their_value(self) -> None:
        """Every option that takes a value spells a metavar of its own.

        click falls back to the type name, so an option declared without one
        reads `--build-dir TEXT`, which says less than the name it replaced.
        The brackets on --graph and --mermaid are what marks their value as
        optional, since click renders those exactly like a required one.
        """
        result = _invoke("--help")
        assert result.exit_code == 0
        assert "-B, --build-dir DIR" in result.stdout
        assert "-b, --build-script FILE" in result.stdout
        assert "-j, --jobs N" in result.stdout
        assert "TEXT" not in result.stdout
        assert "INTEGER" not in result.stdout

        result = _invoke("generate", "--help")
        assert result.exit_code == 0
        assert "--graph [FILE]" in result.stdout
        assert "--mermaid [FILE]" in result.stdout

    def test_subcommand_help(self) -> None:
        """Test that subcommand --help works."""
        result = _invoke("build", "--help")
        assert result.exit_code == 0
        assert "targets" in result.stdout
        assert "--jobs" in result.stdout

    def test_test_subcommand_dispatches_to_runner(self, tmp_path: Path) -> None:
        """`pcons test` hands its argv to pcons.test_runner, which owns them."""
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
        hands the runner a bogus leading "test" positional, which the
        runner rejects. The option's value must be consumed as a value
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

    def test_cache_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        build_dir = self._populate(tmp_path, monkeypatch)
        assert _cache_list(build_dir) == 0
        out = capsys.readouterr().out
        assert "HELLO=42" in out
        assert "variant=debug" in out
        assert "generator=ninja" in out

    def test_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from pcons.core.cache import CACHE_FILE

        build_dir = self._populate(tmp_path, monkeypatch)
        assert _cache_path(build_dir) == 0
        assert str(build_dir / CACHE_FILE) in capsys.readouterr().out

    def test_cache_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        build_dir = self._populate(tmp_path, monkeypatch)
        assert _cache_clear(build_dir) == 0
        capsys.readouterr()
        # After clearing, list shows no settings.
        assert _cache_list(build_dir) == 0
        assert capsys.readouterr().out.strip() == ""

    def test_cache_clear_discards_the_scan_cache_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Both files are answers from an earlier run, so both go."""
        from pcons.toolchains._scan_cache import CACHE_FILE as SCAN_CACHE_FILE

        build_dir = self._populate(tmp_path, monkeypatch)
        scan_cache = build_dir / SCAN_CACHE_FILE
        scan_cache.write_text('{"entries": {}}', encoding="utf-8")

        assert _cache_clear(build_dir) == 0
        assert not scan_cache.exists()
        assert str(scan_cache) in capsys.readouterr().out

    def test_cache_clear_discards_a_scan_cache_left_on_its_own(
        self, tmp_path: Path, capsys
    ) -> None:
        """A build dir can hold scan results and no persisted settings."""
        from pcons.toolchains._scan_cache import CACHE_FILE as SCAN_CACHE_FILE

        build_dir = tmp_path / "build"
        build_dir.mkdir()
        scan_cache = build_dir / SCAN_CACHE_FILE
        scan_cache.write_text('{"entries": {}}', encoding="utf-8")

        assert _cache_clear(build_dir) == 0
        assert not scan_cache.exists()
        assert "No cache" not in capsys.readouterr().out

    def test_cache_show_names_the_source_dir_and_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """`show` is `list` plus where the cache came from.

        Those two lines are the only way to tell a cache written for another
        source tree from a stale one, which is the failure `run_script` warns
        about. Nothing else prints either.
        """
        build_dir = self._populate(tmp_path, monkeypatch)
        assert _cache_show(build_dir) == 0
        out = capsys.readouterr().out
        assert "HELLO=42" in out
        assert f"# source_dir: {tmp_path}" in out
        assert "# cache file:" in out

    def test_cache_show_omits_a_source_dir_it_does_not_have(
        self, tmp_path: Path, capsys
    ) -> None:
        """A cache written before source_dir was recorded still shows what it
        has, rather than printing a line naming nothing."""
        from pcons.core.cache import BuildCache

        build_dir = tmp_path / "build"
        build_dir.mkdir()
        cache = BuildCache(build_dir)
        cache.set("variant", "debug")
        cache.save()
        assert _cache_show(build_dir) == 0
        out = capsys.readouterr().out
        assert "variant" in out
        assert "# source_dir:" not in out
        assert "# cache file:" in out

    @pytest.mark.parametrize("verb", ["list", "show", "clear"])
    def test_a_missing_cache_reports_cleanly(
        self, tmp_path: Path, capsys, verb: str
    ) -> None:
        """`path` is the one verb that answers without a cache to read."""
        work = {"list": _cache_list, "show": _cache_show, "clear": _cache_clear}[verb]
        assert work(tmp_path / "nope") == 0
        assert "No cache" in capsys.readouterr().out


class TestNestingPassesOnTheMerge:
    """The group declares what class its commands and subgroups are, so a
    declaration does not have to remember `cls=`.

    `pcons cache` is one level deep and would still work if a subgroup did not
    pass the classes on. Two levels is where it shows: a plain `click.Group`
    adopts nothing, and an option spelled before the outer name would be lost
    without a word.
    """

    @staticmethod
    def _tree() -> tuple[PconsGroup, list[str]]:
        seen: list[str] = []

        @click.group(cls=PconsGroup, invoke_without_command=True)
        @click.option("-B", "--build-dir", default="build")
        def root(**kw: object) -> None: ...

        @root.group("outer", invoke_without_command=True)
        @click.option("-B", "--build-dir", default="build")
        def outer(**kw: object) -> None: ...

        @outer.group("inner", invoke_without_command=True)
        @click.option("-B", "--build-dir", default="build")
        def inner(**kw: object) -> None: ...

        @inner.command("verb")
        @click.option("-B", "--build-dir", default="build")
        def verb(build_dir: str) -> None:
            seen.append(build_dir)

        return root, seen

    def test_every_level_is_a_merging_class(self) -> None:
        root, _ = self._tree()
        outer = root.commands["outer"]
        assert isinstance(outer, MergingGroup)
        inner = outer.commands["inner"]
        assert isinstance(inner, MergingGroup)
        assert isinstance(inner.commands["verb"], MergingCommand)

    def test_a_value_spelled_at_the_top_reaches_the_deepest_verb(self) -> None:
        root, seen = self._tree()
        result = CliRunner().invoke(
            root, ["-B", "out", "outer", "inner", "verb"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert seen == ["out"]


class TestCacheIsAGroup:
    """`pcons cache` dispatches to a subcommand, not to a positional value.

    The tests above call the work functions, so without these nothing checks
    that a verb on the command line reaches the right one, nor that the build
    directory survives the extra context a nested group introduces.
    """

    @staticmethod
    def _path(*argv: str) -> str:
        result = _invoke(*argv)
        assert result.exit_code == 0, result.output
        return result.stdout.strip()

    def test_each_verb_reaches_its_own_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        from pcons.core.cache import CACHE_FILE

        assert self._path("-B", str(tmp_path), "cache", "path") == str(
            tmp_path / CACHE_FILE
        )
        for verb in ("list", "show", "clear"):
            result = _invoke("-B", str(tmp_path), "cache", verb)
            assert result.exit_code == 0
            assert "No cache" in result.stdout

    def test_no_verb_lists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare `pcons cache` did this when the verb was an optional argument."""
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        bare = _invoke("-B", str(tmp_path), "cache")
        listed = _invoke("-B", str(tmp_path), "cache", "list")
        assert bare.exit_code == listed.exit_code == 0
        assert bare.stdout == listed.stdout

    def test_an_unknown_verb_is_a_usage_error(self, tmp_path: Path) -> None:
        result = _invoke("-B", str(tmp_path), "cache", "bogus")
        assert result.exit_code == 2
        assert "bogus" in result.stderr

    def test_the_help_names_every_verb(self) -> None:
        result = _invoke("cache", "--help")
        assert result.exit_code == 0
        # Declaration order, so read-only verbs come before the destructive one.
        assert [
            line.split()[0]
            for line in result.stdout.partition("Commands:")[2].strip().splitlines()
        ] == ["list", "show", "clear", "path"]

    def test_a_build_dir_spelled_before_cache_reaches_the_verb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The verb is two contexts below the group that owns -B.

        A merge that only reads the immediate parent finds the `cache` group's
        untouched default here, and the verb silently answers for `build/`.
        """
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        from pcons.core.cache import CACHE_FILE

        assert self._path("-B", str(tmp_path), "cache", "path") == str(
            tmp_path / CACHE_FILE
        )

    def test_a_build_dir_spelled_after_cache_also_reaches_the_verb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        from pcons.core.cache import CACHE_FILE

        assert self._path("cache", "-B", str(tmp_path), "path") == str(
            tmp_path / CACHE_FILE
        )

    def test_the_later_build_dir_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        from pcons.core.cache import CACHE_FILE

        later = tmp_path / "later"
        assert self._path(
            "-B", str(tmp_path / "earlier"), "cache", "-B", str(later), "path"
        ) == str(later / CACHE_FILE)

    def test_a_spelled_build_dir_beats_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The merge tests where a value came from, not whether it is a default.

        $PCONS_BUILD_DIR reaches every level, so comparing values cannot tell
        an inherited `-B` from an environment default two contexts down.
        """
        from pcons.core.cache import CACHE_FILE

        monkeypatch.setenv("PCONS_BUILD_DIR", str(tmp_path / "from_env"))
        spelled = tmp_path / "spelled"
        assert self._path("-B", str(spelled), "cache", "path") == str(
            spelled / CACHE_FILE
        )
        # With nothing spelled, the environment still decides.
        assert self._path("cache", "path") == str(tmp_path / "from_env" / CACHE_FILE)


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
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-B", "out", "generate").exit_code == 0
        assert seen[0]["build_dir"] == Path("out")

    def test_build_dir_after_the_command_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-B", "out", "generate", "-B", "late").exit_code == 0
        assert seen[0]["build_dir"] == Path("late")

    def test_build_dir_defaults_when_given_nowhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("generate").exit_code == 0
        assert seen[0]["build_dir"] == Path("build")

    def test_build_dir_from_the_environment_loses_to_the_command_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The subcommand's own value comes from the environment, not the
        command line, so it must not beat a -B spelled before the command."""
        monkeypatch.setenv("PCONS_BUILD_DIR", "from-env")
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-B", "out", "generate").exit_code == 0
        assert seen[0]["build_dir"] == Path("out")

    def test_verbose_before_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-v", "generate").exit_code == 0
        assert seen[0]["verbose"] is True

    def test_variant_before_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _capture_command(monkeypatch, cli_build)
        assert _invoke("--variant", "release", "build").exit_code == 0
        assert seen[0]["variant"] == "release"

    def test_command_only_options_are_unaffected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_args(monkeypatch, "_clean")
        assert _invoke("clean", "--all").exit_code == 0
        assert seen[0]["everything"] is True
        assert _invoke("clean").exit_code == 0
        assert seen[1]["everything"] is False


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
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-G", "make", "generate").exit_code == 0
        assert not ran_default
        assert seen
        assert seen[0]["generator"] == ("make",)

    def test_long_generator_before_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("--generator", "make", "generate").exit_code == 0
        assert not ran_default
        assert seen

    def test_option_value_that_names_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_build)
        # cli_build is also where the catch-all ends up, so the guard is what
        # says `build` resolved as a command rather than as a target named
        # "build" with "test" for a build directory.
        assert _invoke("-G", "make", "-B", "test", "build").exit_code == 0
        assert not ran_default
        assert seen[0]["build_dir"] == Path("test")

    def test_bundled_short_option_does_not_hide_its_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The scan skipped an option's value by matching the whole token, so
        # `-vC` was not the `-C` that takes one: `test` read as the command
        # and -C then chdir'd into `generate`.
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("CC=clang", "-vB", "test", "generate").exit_code == 0
        assert not ran_default
        assert seen[0]["build_dir"] == Path("test")
        assert seen[0]["verbose"] is True

    def test_a_flag_does_not_swallow_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("CC=clang", "-v", "generate").exit_code == 0
        assert not ran_default
        assert seen[0]["verbose"] is True

    def test_attached_short_option_value_is_not_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("CC=clang", "-Btest", "generate").exit_code == 0
        assert not ran_default
        assert seen[0]["build_dir"] == Path("test")

    def test_long_option_value_spelled_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("CC=clang", "--build-dir=test", "generate").exit_code == 0
        assert not ran_default
        assert seen[0]["build_dir"] == Path("test")


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


class TestBuildDirForwardedToTheRunner:
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

    def test_build_dir_without_a_value_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dangling -B is rejected, not tolerated: nothing runs behind it."""
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


class TestDoubleDashEscape:
    """`--` marks the rest of argv as targets, dashes and all.

    click's group parser consumes the `--` while reading the group's own
    options, so without help the token after it is parsed as an option again
    and `pcons -- -foo` fails with "No such option: -f".

    A command name is not rescued: it cannot start with a dash, so the escape
    never reaches one.
    """

    @pytest.mark.parametrize(
        ("argv", "extra"),
        [
            (["--", "-foo"], ["-foo"]),
            (["--", "-j"], ["-j"]),
            (["--", "--verbose"], ["--verbose"]),
            (["--", "-foo", "-bar"], ["-foo", "-bar"]),
            (["--", "--"], ["--"]),
            (["--", "-B", "out"], ["-B", "out"]),
            (["--", "-foo", "generate"], ["-foo", "generate"]),
            (["--", "CC=clang"], ["CC=clang"]),
            (["hello", "--", "-foo"], ["hello", "-foo"]),
            (["--"], []),
        ],
    )
    def test_targets_survive_the_escape(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str], extra: list[str]
    ) -> None:
        seen = _capture_command(monkeypatch, cli_default)
        assert _invoke(*argv).exit_code == 0
        assert list(seen[0]["extra"]) == extra

    def test_escaped_options_are_not_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_command(monkeypatch, cli_default)
        assert _invoke("--", "--verbose", "-B", "out").exit_code == 0
        assert seen[0]["verbose"] is False
        assert seen[0]["build_dir"] == Path("build")

    def test_a_command_name_is_still_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran_default = _capture_command(monkeypatch, cli_default)
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("-B", "out", "--", "generate").exit_code == 0
        assert not ran_default
        assert seen
        assert seen[0]["build_dir"] == Path("out")

    def test_the_escape_protects_a_target_named_after_a_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The escape only reaches the scan for a command name spelled late,
        # since click's own parser eats the `--` before that. Without it the
        # scan read `clean` as the command and deleted the build directory.
        ran_clean = _capture_command(monkeypatch, cli_clean)
        seen = _capture_command(monkeypatch, cli_default)
        assert _invoke("CC=clang", "--", "clean").exit_code == 0
        assert not ran_clean
        assert list(seen[0]["extra"]) == ["CC=clang", "clean"]

    def test_a_typo_without_the_escape_is_still_an_error(self) -> None:
        assert _invoke("hello", "--nope").exit_code == 2
        assert _invoke("--nope").exit_code == 2


class TestCatchAllNameIsNotReserved:
    """The catch-all command's name is internal, so a target may use it.

    click resolves a registered command name before any fallback runs, so the
    hidden command's own name would otherwise be swallowed here rather than
    built, and silently: it is not a command a user can be told about.
    """

    @pytest.mark.parametrize(
        ("argv", "extra"),
        [
            (["_default"], ["_default"]),
            (["_default", "hello"], ["_default", "hello"]),
            (["_default", "CC=clang"], ["_default", "CC=clang"]),
            (["--", "_default"], ["_default"]),
            (["-B", "out", "_default"], ["_default"]),
        ],
    )
    def test_it_is_an_ordinary_target(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str], extra: list[str]
    ) -> None:
        seen = _capture_command(monkeypatch, cli_default)
        assert _invoke(*argv).exit_code == 0
        assert list(seen[0]["extra"]) == extra

    def test_a_real_command_still_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the catch-all's own name is refused, not every name."""
        seen = _capture_command(monkeypatch, cli_generate)
        assert _invoke("generate").exit_code == 0
        assert seen


class TestDoubleDashBeforeTheRunner:
    """`pcons test` consumes one `--` and passes any further one on.

    Everything after `test` reaches the test runner untouched, so the only job
    left for the separator is to shield a token the CLI would otherwise claim
    as its own, which means `-C` and `--help`. It does that job and is then
    spent, the way a wrapper conventionally treats it. A runner argument that
    has to be a literal `--` is written as a second one.

    The old parser forwarded the separator instead, so `pcons test -- --list`
    reached the runner as a positional it has no use for and errored.
    """

    @pytest.mark.parametrize(
        ("argv", "forwarded"),
        [
            (["test", "-x"], ["-x"]),
            (["test", "--", "-x"], ["-x"]),
            (["test", "--", "--list"], ["--list"]),
            (["test", "--", "-C", "sub"], ["-C", "sub"]),
            (["test", "--", "--", "-x"], ["--", "-x"]),
            (["test", "--list", "--", "-x"], ["--list", "-x"]),
            (["-B", "out", "test", "--", "-x"], ["-B", "out", "-x"]),
        ],
    )
    def test_one_separator_is_consumed(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str], forwarded: list[str]
    ) -> None:
        seen = _capture_test_runner(monkeypatch)
        assert _invoke(*argv).exit_code == 0
        assert seen == [forwarded]

    def test_the_separator_is_what_makes_dash_c_reach_the_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unescaped, -C is the CLI's own option and the runner never sees it."""
        monkeypatch.chdir(tmp_path)
        seen = _capture_test_runner(monkeypatch)
        assert _invoke("test", "-C", str(tmp_path)).exit_code == 0
        assert seen == [[]]


class TestCatchAllUsageLine:
    """The catch-all command is hidden, so it reports the group's path.

    click builds a command path as "<parent> <name>" and only lstrips it, so
    the nameless catch-all used to render "pcons  " with two spaces on the
    commonest error path there is, a target plus a mistyped option.
    """

    def test_error_usage_names_the_program_once(self) -> None:
        result = _invoke("hello", "--nope")
        assert result.exit_code == 2
        assert "Usage: cli [OPTIONS] [EXTRA]...\n" in result.stderr
        assert "Try 'cli --help' for help.\n" in result.stderr
        assert "cli  " not in result.stderr

    def test_help_usage_names_the_program_once(self) -> None:
        result = _invoke("hello", "--help")
        assert result.exit_code == 0
        assert result.stdout.startswith("Usage: cli [OPTIONS] [EXTRA]...\n")


class TestCommandInvokedWithoutTheGroup:
    """A command object invoked on its own still runs.

    MergingCommand reads the values spelled before the command name off the
    parent context. Invoking the command object directly, which a test can do
    and the CLI never does, leaves it without a parent. Nothing else exercises
    the guard that allows it.
    """

    def test_a_merging_command_has_no_parent_to_merge_from(
        self, tmp_path: Path
    ) -> None:
        # A leaf of the cache group, so invoking it alone skips both the group
        # it normally sits under and the one above that.
        from pcons.core.cache import CACHE_FILE

        result = CliRunner().invoke(
            cli_cache_path, ["-B", str(tmp_path)], catch_exceptions=False
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == str(tmp_path / CACHE_FILE)

    def test_the_catch_all_reports_its_own_path_without_a_group(self) -> None:
        """It borrows the group's command path, and there is none to borrow."""
        result = CliRunner().invoke(cli_default, ["--help"], catch_exceptions=False)
        assert result.exit_code == 0
        assert result.stdout.startswith("Usage: _default [OPTIONS] [EXTRA]...\n")


class TestProductionEntryPoint:
    """main() is what the console script calls, and CliRunner cannot reach it.

    Everything else here runs under CliRunner, which drives
    cli.main(standalone_mode=True). main() passes standalone_mode=False and
    handles the outcome itself: delete e.show() from it and every usage error
    goes silent in production with the whole suite still green.
    """

    def test_a_usage_error_is_reported_and_exits_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _out, err = _main(capsys, "--bogus")
        assert code == 2
        assert "No such option '--bogus'" in err

    def test_the_error_names_the_program_once(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """prog_name is only visible here: CliRunner calls the program 'cli'.

        An error is what pins it. `pcons --version` cannot: click's version
        option memoizes the program name in its own closure the first time it
        runs, so within one interpreter it reports whichever name got there
        first.
        """
        code, _out, err = _main(capsys, "hello", "--nope")
        assert code == 2
        assert "Usage: pcons [OPTIONS] [EXTRA]...\n" in err
        assert "Try 'pcons --help' for help.\n" in err
        assert "pcons  " not in err

    def test_a_command_result_becomes_the_exit_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """standalone_mode=False returns what the command returned, so main()
        is the only thing turning it into a process exit code."""
        monkeypatch.chdir(tmp_path)
        code, _out, err = _main(capsys)
        assert code == 1
        assert "No pcons-build.py found" in err

    def test_an_interrupt_exits_130_without_a_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def interrupted(**kw: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_generate, "callback", interrupted)
        code, out, err = _main(capsys, "generate")
        assert code == 130
        assert "Traceback" not in out + err


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


def _at(path: Path, when: float) -> Path:
    """Stamp *path* with an explicit mtime, so no test races the clock."""
    os.utime(path, (when, when))
    return path


class TestNeedsGeneration:
    """Whether an existing build directory is out of date.

    This decides, with nothing printed either way, whether `pcons build`
    reruns the build script first. Break it in one direction and an edit to
    pcons-build.py never reaches the build; break it in the other and every
    build regenerates.
    """

    def test_no_build_files_at_all(self, tmp_path: Path) -> None:
        assert _needs_generation(tmp_path) is True

    def test_a_script_newer_than_the_build_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "build.ninja").write_text("")
        _at(tmp_path / "build.ninja", 1000)
        (tmp_path / "pcons-build.py").write_text("")
        _at(tmp_path / "pcons-build.py", 2000)
        assert _needs_generation(tmp_path) is True

    def test_a_script_older_than_the_build_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "build.ninja").write_text("")
        _at(tmp_path / "build.ninja", 2000)
        (tmp_path / "pcons-build.py").write_text("")
        _at(tmp_path / "pcons-build.py", 1000)
        assert _needs_generation(tmp_path) is False

    def test_a_makefile_is_a_build_file_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The make generator writes no build.ninja, so a build directory it
        made must not read as empty and regenerate on every build."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "Makefile").write_text("")
        _at(tmp_path / "Makefile", 2000)
        (tmp_path / "pcons-build.py").write_text("")
        _at(tmp_path / "pcons-build.py", 1000)
        assert _needs_generation(tmp_path) is False

    def test_an_xcode_project_is_a_build_file_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Xcode's output is a directory, not a file, so it needs its own check."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "demo.xcodeproj").mkdir()
        _at(tmp_path / "demo.xcodeproj", 2000)
        (tmp_path / "pcons-build.py").write_text("")
        _at(tmp_path / "pcons-build.py", 1000)
        assert _needs_generation(tmp_path) is False

    def test_a_named_script_that_is_missing(self, tmp_path: Path) -> None:
        """-b names a script that is not there: regenerate, and let generate
        report it. Answering False would build stale files and say nothing."""
        (tmp_path / "build.ninja").write_text("")
        missing = str(tmp_path / "nope.py")
        assert _needs_generation(tmp_path, build_script=missing) is True

    def test_no_script_anywhere_leaves_the_build_files_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A build directory can be used without its source tree; there is
        nothing to regenerate from, so the existing files stand."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "build.ninja").write_text("")
        assert _needs_generation(tmp_path) is False


class TestFindNinjaRunner:
    """Which program a build actually runs.

    Every failure here is silent: --ninja or $NINJA quietly ignored and the
    default used instead, or the uvx fallback lost so pcons declares ninja
    missing on a machine that has uv.
    """

    @staticmethod
    def _which(monkeypatch: pytest.MonkeyPatch, table: dict[str, str]) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: table.get(name))

    def test_an_override_is_resolved_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {"n2": "/opt/n2", "ninja": "/usr/bin/ninja"})
        assert _find_ninja("n2") == ["/opt/n2"]

    def test_an_override_may_be_an_absolute_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A runner outside PATH is named by path, and which() will not find it."""
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {})
        runner = str(tmp_path / "n2")
        assert _find_ninja(runner) == [runner]

    def test_an_unresolvable_override_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not a silent fall back to ninja: the user asked for another runner."""
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {"ninja": "/usr/bin/ninja"})
        assert _find_ninja("n2") is None

    def test_the_ninja_env_var_names_the_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NINJA", "n2")
        self._which(monkeypatch, {"n2": "/opt/n2", "ninja": "/usr/bin/ninja"})
        assert _find_ninja() == ["/opt/n2"]

    def test_an_explicit_override_beats_the_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NINJA", "n2")
        self._which(monkeypatch, {"n2": "/opt/n2", "samu": "/opt/samu"})
        assert _find_ninja("samu") == ["/opt/samu"]

    def test_ninja_on_path_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {"ninja": "/usr/bin/ninja", "uvx": "/usr/bin/uvx"})
        assert _find_ninja() == ["/usr/bin/ninja"]

    def test_uvx_is_the_fallback_when_ninja_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {"uvx": "/usr/bin/uvx"})
        assert _find_ninja() == ["/usr/bin/uvx", "ninja"]

    def test_nothing_to_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NINJA", raising=False)
        self._which(monkeypatch, {})
        assert _find_ninja() is None


class TestBuildToolCommandLines:
    """What pcons hands the build tool.

    All argv assembly. A dropped -j or a dropped target still exits 0, so
    the visible failure is a build that quietly did the wrong amount of
    work rather than an error.
    """

    @staticmethod
    def _record(
        monkeypatch: pytest.MonkeyPatch, returncode: int = 0
    ) -> list[list[str]]:
        seen: list[list[str]] = []

        def fake_run(cmd: list[str], *a: object, **kw: object) -> object:
            seen.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode)

        monkeypatch.setattr(subprocess, "run", fake_run)
        return seen

    @staticmethod
    def _only(monkeypatch: pytest.MonkeyPatch, name: str, path: str) -> None:
        monkeypatch.delenv("NINJA", raising=False)
        monkeypatch.setattr(shutil, "which", lambda n: path if n == name else None)

    def test_ninja_gets_the_build_dir_jobs_verbose_and_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        self._only(monkeypatch, "ninja", "/usr/bin/ninja")
        seen = self._record(monkeypatch, returncode=7)
        assert run_ninja(tmp_path, targets=["a", "b"], jobs=3, verbose=True) == 7
        assert seen == [
            ["/usr/bin/ninja", "-C", str(tmp_path), "-j", "3", "-v", "a", "b"]
        ]

    def test_ninja_gets_only_the_build_dir_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        self._only(monkeypatch, "ninja", "/usr/bin/ninja")
        seen = self._record(monkeypatch)
        assert run_ninja(tmp_path) == 0
        assert seen == [["/usr/bin/ninja", "-C", str(tmp_path)]]

    def test_ninja_needs_a_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        assert run_ninja(tmp_path) == 1
        assert seen == []

    def test_a_missing_ninja_is_reported_not_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        self._only(monkeypatch, "nothing", "")
        seen = self._record(monkeypatch)
        assert run_ninja(tmp_path) == 1
        assert seen == []

    def test_make_gets_the_build_dir_jobs_and_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "Makefile").write_text("")
        self._only(monkeypatch, "make", "/usr/bin/make")
        seen = self._record(monkeypatch, returncode=2)
        assert run_make(tmp_path, targets=["a"], jobs=4) == 2
        assert seen == [["/usr/bin/make", "-C", str(tmp_path), "-j", "4", "a"]]

    def test_make_needs_a_makefile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        assert run_make(tmp_path) == 1
        assert seen == []

    def test_a_missing_make_is_reported_not_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "Makefile").write_text("")
        self._only(monkeypatch, "nothing", "")
        seen = self._record(monkeypatch)
        assert run_make(tmp_path) == 1
        assert seen == []

    def test_xcodebuild_maps_targets_jobs_and_the_variant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Xcode spells all of these differently from ninja, and the variant
        becomes a configuration name rather than being passed through."""
        (tmp_path / "demo.xcodeproj").mkdir()
        self._only(monkeypatch, "xcodebuild", "/usr/bin/xcodebuild")
        seen = self._record(monkeypatch, returncode=3)
        code = run_xcodebuild(
            tmp_path, targets=["a", "b"], jobs=2, configuration="debug"
        )
        assert code == 3
        assert seen == [
            [
                "/usr/bin/xcodebuild",
                "-project",
                str(tmp_path / "demo.xcodeproj"),
                "-configuration",
                "Debug",
                "-jobs",
                "2",
                "-target",
                "a",
                "-target",
                "b",
                "-quiet",
            ]
        ]

    def test_xcodebuild_defaults_to_release_and_speaks_up_when_verbose(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-quiet is the default, so verbose is spelled by its absence."""
        (tmp_path / "demo.xcodeproj").mkdir()
        self._only(monkeypatch, "xcodebuild", "/usr/bin/xcodebuild")
        seen = self._record(monkeypatch)
        assert run_xcodebuild(tmp_path, verbose=True) == 0
        assert seen == [
            [
                "/usr/bin/xcodebuild",
                "-project",
                str(tmp_path / "demo.xcodeproj"),
                "-configuration",
                "Release",
            ]
        ]

    def test_xcodebuild_needs_a_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        assert run_xcodebuild(tmp_path) == 1
        assert seen == []

    def test_a_missing_xcodebuild_is_reported_not_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "demo.xcodeproj").mkdir()
        self._only(monkeypatch, "nothing", "")
        seen = self._record(monkeypatch)
        assert run_xcodebuild(tmp_path) == 1
        assert seen == []


class TestCleanRunsTheBuildTool:
    """`pcons clean` without --all delegates to the runner.

    The three tests below are the whole non---all path: nothing else runs
    it, because every other clean test either passes --all or stops at the
    missing build.ninja.
    """

    def test_clean_asks_ninja_to_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        TestBuildToolCommandLines._only(monkeypatch, "ninja", "/usr/bin/ninja")
        seen = TestBuildToolCommandLines._record(monkeypatch, returncode=5)
        assert _clean(tmp_path, everything=False, ninja=None) == 5
        assert seen == [["/usr/bin/ninja", "-C", str(tmp_path), "-t", "clean"]]

    def test_clean_refuses_n2_rather_than_running_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """n2 has no `-t clean`. Running it anyway would fail with n2's own
        message, which says nothing about `pcons clean --all` being the way out."""
        (tmp_path / "build.ninja").write_text("")
        TestBuildToolCommandLines._only(monkeypatch, "n2", "/opt/n2")
        seen = TestBuildToolCommandLines._record(monkeypatch)
        assert _clean(tmp_path, everything=False, ninja="n2") == 1
        assert seen == []

    def test_clean_without_a_runner_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        TestBuildToolCommandLines._only(monkeypatch, "nothing", "")
        seen = TestBuildToolCommandLines._record(monkeypatch)
        assert _clean(tmp_path, everything=False, ninja=None) == 1
        assert seen == []

    def test_clean_all_succeeds_with_nothing_to_remove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`pcons clean --all` runs unconditionally in CI scripts, so a tree
        that was never built has to be a success rather than an error."""
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _invoke("clean", "--all").exit_code == 0


class TestGraphOptionsReachTheBuildScript:
    """--graph and --mermaid are delivered to the script as environment.

    Two lines in cmd_generate are the only link between the click option and
    PCONS_GRAPH. Drop either and `pcons generate --graph g.dot` still exits
    0, having written no graph and said nothing.
    """

    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
        seen: list[dict[str, object]] = []

        def fake_run_script(
            script: Path, build_dir: Path, **kw: object
        ) -> tuple[int, list[object]]:
            seen.append(kw)
            return 0, []

        monkeypatch.setattr("pcons.cli.run_script", fake_run_script)
        return seen

    @staticmethod
    def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pcons-build.py").write_text("")

    def test_named_files_become_the_two_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._project(tmp_path, monkeypatch)
        seen = self._record(monkeypatch)
        result = _invoke("generate", "--graph", "g.dot", "--mermaid", "m.mmd")
        assert result.exit_code == 0
        assert seen[0]["extra_env"] == {
            "PCONS_GRAPH": "g.dot",
            "PCONS_MERMAID": "m.mmd",
        }

    def test_a_bare_flag_asks_for_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--graph takes an optional value; bare, it means "-"."""
        self._project(tmp_path, monkeypatch)
        seen = self._record(monkeypatch)
        assert _invoke("generate", "--graph").exit_code == 0
        assert seen[0]["extra_env"] == {"PCONS_GRAPH": "-"}

    def test_neither_option_sends_no_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._project(tmp_path, monkeypatch)
        seen = self._record(monkeypatch)
        assert _invoke("generate").exit_code == 0
        assert seen[0]["extra_env"] is None


class TestLoggingIsSetUpFromTheMergedOptions:
    """The merging invoke configures logging, so no command opens by doing it.

    Two things go wrong if it configures for a command that declares neither
    option. `pcons test` hands its argv to a separate program with its own
    logging, so validating --debug there rejects a command line the CLI used
    to accept. And a command with no -v of its own would settle a level that
    beats one spelled before its name.
    """

    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, str | None]]:
        """Record every setup_logging call. _invoke restores the root logger
        afterwards, so the level itself cannot be read once it returns."""
        seen: list[tuple[bool, str | None]] = []
        monkeypatch.setattr(
            "pcons.cli.setup_logging",
            lambda verbose, debug: seen.append((verbose, debug)),
        )
        return seen

    def test_verbose_reaches_a_command_that_declares_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        _invoke("-v", "clean")
        assert seen == [(True, None)]

    def test_a_command_without_it_stays_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        _invoke("clean")
        assert seen == [(False, None)]

    @pytest.mark.parametrize("argv", [["-v", "cache", "list"], ["cache", "-v", "list"]])
    def test_either_spelling_around_a_subgroup(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        """The group configures, then the verb configures again from its own
        merged values. Both must see the -v, wherever it was spelled."""
        seen = self._record(monkeypatch)
        _invoke(*argv)
        assert seen == [(True, None), (True, None)]

    def test_a_command_declaring_neither_option_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _capture_test_runner(monkeypatch)
        seen = self._record(monkeypatch)
        _invoke("-v", "test", "--list")
        assert seen == []

    def test_debug_is_not_validated_for_the_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`pcons --debug bogus test` runs the runner, which owns everything
        after `test`. Rejecting the subsystem name here would exit 1 with the
        runner never seeing its argv."""
        seen = _capture_test_runner(monkeypatch)
        assert _invoke("--debug", "bogus", "test", "--list").exit_code == 0
        assert seen == [["--list"]]


class TestDebugSpecReachesTheShell:
    """A bad --debug or PCONS_DEBUG comes out of the entry point, not past it.

    init_debug used to print and raise SystemExit from inside the command
    callback, so the code the shell saw came from neither `main` nor click.
    """

    def test_unknown_subsystem_is_a_usage_error(self) -> None:
        result = _invoke("--debug", "bogus", "cache", "path")
        assert result.exit_code == 2
        assert "Unknown debug subsystem(s): bogus" in result.output
        assert "configure" in result.output

    def test_unknown_subsystem_names_only_the_unknown_ones(self) -> None:
        result = _invoke("--debug", "resolve,bogus", "cache", "path")
        assert result.exit_code == 2
        assert "Unknown debug subsystem(s): bogus" in result.output

    def test_help_lists_the_subsystems_and_exits_zero(self) -> None:
        result = _invoke("--debug", "help", "cache", "path")
        assert result.exit_code == 0
        assert "Available debug subsystems" in result.output

    def test_the_environment_variable_takes_the_same_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PCONS_DEBUG", "bogus")
        result = _invoke("cache", "path")
        assert result.exit_code == 2
        assert "Unknown debug subsystem(s): bogus" in result.output

    def test_main_returns_the_code_rather_than_exiting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of the change: `main` produces it, so it can be read."""
        monkeypatch.setenv("PCONS_DEBUG", "bogus")
        assert cli_module.main(["cache", "path"]) == 2


class TestModulesAreLoadedWhereTheyAreDeclared:
    """Loading runs each module's register(), so only a command that works
    from the build script asks for it. `pcons clean` runs no user code."""

    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch) -> list[object]:
        seen: list[object] = []
        monkeypatch.setattr(
            "pcons.cli._load_user_modules", lambda path: seen.append(path)
        )
        return seen

    @pytest.mark.parametrize("argv", [["generate"], ["build"], ["info"], ["hello"], []])
    def test_a_command_that_reaches_the_build_script_loads_them(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        for command in (cli_generate, cli_build, cli_info, cli_default):
            _capture_command(monkeypatch, command)
        seen = self._record(monkeypatch)
        _invoke(*argv)
        # A bare `pcons` reaches the catch-all through forward(), which calls
        # the callback rather than the command, so this covers a path the
        # merging invoke never sees.
        assert seen == [None]

    @pytest.mark.parametrize(
        "argv", [["clean"], ["init"], ["cache", "list"], ["cache"], ["test", "--list"]]
    )
    def test_a_command_that_does_not_leaves_them_alone(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        _capture_test_runner(monkeypatch)
        for command in (cli_clean, cli_init):
            _capture_command(monkeypatch, command)
        seen = self._record(monkeypatch)
        _invoke(*argv)
        assert seen == []

    def test_the_search_path_reaches_the_loader(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _capture_command(monkeypatch, cli_generate)
        seen = self._record(monkeypatch)
        _invoke("--modules-path", "one", "generate")
        assert seen == ["one"]


class TestModulesSearchPath:
    """--modules-path carries several directories, separated the way PATH is.

    Split it wrong and the second directory's modules are silently absent,
    so a toolchain the user wrote never registers and the build falls back
    to a built-in one.
    """

    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch) -> list[object]:
        seen: list[object] = []

        def fake_load(extra_paths: object = None) -> dict[str, object]:
            seen.append(extra_paths)
            return {}

        monkeypatch.setattr("pcons.modules.load_modules", fake_load)
        return seen

    def test_the_search_path_is_split_on_the_path_separator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        spec = os.pathsep.join(["one", "two"])
        _load_user_modules(spec)
        assert seen == [["one", "two"]]

    def test_no_search_path_loads_the_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._record(monkeypatch)
        _load_user_modules(None)
        assert seen == [None]


class TestBuildDirectoryChosenByTheScript:
    """A build script may pick a build directory other than the requested one.

    Both build entry points re-read it off the resolved Project afterwards.
    Drop that and the build runs in the empty directory that was asked for,
    reporting no build files immediately after a successful generate.
    """

    def test_the_default_command_builds_where_the_script_put_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        monkeypatch.setattr(
            "pcons.cli._generate",
            lambda *a, **k: (0, SimpleNamespace(build_dir=elsewhere)),
        )
        seen = _capture_args(monkeypatch, "_build", result=(0, elsewhere))
        assert _invoke("-B", str(tmp_path / "asked")).exit_code == 0
        assert seen[0]["build_dir"] == elsewhere

    def test_a_regenerated_build_runs_where_the_script_put_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "build.ninja").write_text("")
        (tmp_path / "pcons-build.py").write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "pcons.cli._generate",
            lambda *a, **k: (0, SimpleNamespace(build_dir=elsewhere)),
        )
        seen: list[Path] = []

        def fake_run_ninja(build_dir: Path, **kw: object) -> int:
            seen.append(build_dir)
            return 0

        monkeypatch.setattr("pcons.cli.run_ninja", fake_run_ninja)
        assert _invoke("-B", str(tmp_path / "asked"), "build").exit_code == 0
        assert seen == [elsewhere]

    def test_watch_generates_through_the_build_and_not_beside_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A watch loop regenerates only when the build files are stale, which
        the build itself decides. A separate generate here would run the script
        twice on the first pass and once more on every later one."""

        def refuse(*a: object, **k: object) -> tuple[int, object]:
            raise AssertionError("generate ran beside the build")

        monkeypatch.setattr("pcons.cli._generate", refuse)
        seen = _capture_args(monkeypatch, "_watch")
        assert _invoke("-B", str(tmp_path), "--watch").exit_code == 0
        assert len(seen) == 1


class TestXcodeConfiguration:
    """Xcode picks its configuration at build time, unlike ninja and make,
    where the variant is baked into the generated files.

    So a bare `pcons build` has to recover it from the cache. Without that
    lookup, building after `pcons --variant debug generate` quietly produces
    Release.
    """

    def test_a_bare_build_keeps_the_variant_it_was_generated_with(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.core.cache import BuildCache

        build_dir = tmp_path / "build"
        (build_dir / "demo.xcodeproj").mkdir(parents=True)
        cache = BuildCache(build_dir)
        cache.set("variant", "debug")
        cache.save()

        seen: list[str | None] = []

        def fake_run_xcodebuild(
            build_dir: Path, configuration: str | None = None, **kw: object
        ) -> int:
            seen.append(configuration)
            return 0

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("pcons.cli.run_xcodebuild", fake_run_xcodebuild)
        assert _invoke("-B", str(build_dir), "build").exit_code == 0
        assert seen == ["debug"]

    def test_an_explicit_variant_wins_over_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.core.cache import BuildCache

        build_dir = tmp_path / "build"
        (build_dir / "demo.xcodeproj").mkdir(parents=True)
        cache = BuildCache(build_dir)
        cache.set("variant", "debug")
        cache.save()

        seen: list[str | None] = []

        def fake_run_xcodebuild(
            build_dir: Path, configuration: str | None = None, **kw: object
        ) -> int:
            seen.append(configuration)
            return 0

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("pcons.cli.run_xcodebuild", fake_run_xcodebuild)
        assert (
            _invoke("-B", str(build_dir), "--variant", "release", "build").exit_code
            == 0
        )
        assert seen == ["release"]


class TestInheritedVariables:
    """A nested pcons run inherits PCONS_VARS as a JSON object.

    It is tolerated rather than trusted: the value comes from an environment
    pcons does not own, and a malformed one must not take down every run in
    the shell that exported it.
    """

    def test_a_malformed_blob_is_ignored(self) -> None:
        assert _parse_pcons_vars("{not json") == {}

    def test_a_json_value_that_is_not_an_object_is_ignored(self) -> None:
        assert _parse_pcons_vars('["A"]') == {}

    def test_nothing_inherited(self) -> None:
        assert _parse_pcons_vars(None) == {}

    def test_an_object_is_read(self) -> None:
        assert _parse_pcons_vars('{"A": "1"}') == {"A": "1"}


class TestNamedBuildScriptErrors:
    """-b names a script that has to exist.

    Reported by name rather than falling back to the script in the current
    directory, which would generate or describe a different project than the
    one asked for.
    """

    def test_generate_reports_a_missing_named_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "pcons-build.py").write_text("from pcons import Project\n")
        monkeypatch.chdir(tmp_path)
        result = _invoke("generate", "-b", "nope.py")
        assert result.exit_code == 1
        assert "Build script not found: nope.py" in result.stderr

    def test_info_reports_a_missing_named_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "pcons-build.py").write_text('"""Other project."""\n')
        monkeypatch.chdir(tmp_path)
        result = _invoke("info", "-b", "nope.py")
        assert result.exit_code == 1
        assert "Build script not found: nope.py" in result.stderr
        assert "Other project" not in result.stdout

    def test_info_reports_a_syntax_error_rather_than_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """info parses the script to read its docstring. A half-written one is
        the normal case for `pcons info`, so it gets a message, not a traceback."""
        (tmp_path / "pcons-build.py").write_text("def (:\n")
        monkeypatch.chdir(tmp_path)
        result = _invoke("info")
        assert result.exit_code == 1
        assert "Failed to parse" in result.stderr

    def test_info_targets_carries_the_scripts_exit_code_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--targets runs the script for real, so a script that bails takes the
        command with it, code and all. The listing must not print either: a
        half-run script has half the targets, which reads as the whole set."""
        (tmp_path / "pcons-build.py").write_text(
            "import sys\nfrom pcons import Project\nProject('demo')\nsys.exit(3)\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("info", "--targets")
        assert result.exit_code == 3
        assert "Targets:" not in result.stdout

    def test_info_targets_reports_a_missing_named_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--targets has to find the script itself, since it runs it rather
        than reading its docstring. A -b that names nothing is reported by
        name, not by falling back to the one in the directory."""
        (tmp_path / "pcons-build.py").write_text("from pcons import Project\n")
        monkeypatch.chdir(tmp_path)
        result = _invoke("info", "--targets", "-b", "nope.py")
        assert result.exit_code == 1
        assert "Build script not found: nope.py" in result.stderr

    def test_info_targets_reports_an_empty_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = _invoke("info", "--targets")
        assert result.exit_code == 1
        assert "No pcons-build.py found" in result.stderr


class TestWatchReachesTheWatcher:
    """`pcons build --watch` builds once and then watches.

    The catch-all has its own watch branch, so a `build` that dropped the
    option would still work for a bare `pcons` and quietly build once here.
    """

    @staticmethod
    def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
        seen: list[dict[str, object]] = []
        monkeypatch.setattr(
            "pcons.cli._watch",
            lambda **kw: seen.append(kw) or 0,
        )
        return seen

    def test_build_watch_hands_over_the_script_and_the_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\n")
        monkeypatch.chdir(tmp_path)
        seen = self._record(monkeypatch)
        assert _invoke("build", "--watch", "hello").exit_code == 0
        assert len(seen) == 1
        assert seen[0]["script"] == script
        assert seen[0]["targets"] == ["hello"]
        assert callable(seen[0]["build"])

    def test_without_the_flag_nothing_watches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        seen = self._record(monkeypatch)
        _capture_args(monkeypatch, "_build", result=(0, tmp_path))
        assert _invoke("build").exit_code == 0
        assert seen == []


class TestTheNoCommandPathNeedsABuildScript:
    """A bare `pcons` generates, so it says when there is nothing to generate.

    `pcons FOO=bar` has always reported the missing script, because the
    variable leaves no target behind; a target name took a different path and
    reached the build tool, which reported missing build files instead. Same
    state, two stories, and the second one names the consequence rather than
    the cause.
    """

    def test_a_target_names_the_missing_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        seen = _capture_args(monkeypatch, "_build", result=(0, tmp_path))
        result = _invoke("hello")
        assert result.exit_code == 1
        assert "No pcons-build.py found" in result.stderr
        assert seen == []

    def test_watch_is_refused_rather_than_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag was silently ignored here, so the build ran once and the
        watch the user asked for never started."""
        monkeypatch.chdir(tmp_path)
        watched = _capture_args(monkeypatch, "_watch")
        result = _invoke("--watch", "hello")
        assert result.exit_code == 1
        assert "No pcons-build.py found" in result.stderr
        assert watched == []

    def test_a_named_script_that_is_missing_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-b gets past the guard, so the report is of the file the user
        actually asked for rather than of pcons-build.py."""
        monkeypatch.chdir(tmp_path)
        result = _invoke("-b", "nope.py", "hello")
        assert result.exit_code == 1
        assert "Build script not found: nope.py" in result.stderr

    def test_build_still_drives_existing_files_without_a_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard is on the generating path only. `pcons build` runs the
        build tool over whatever is already there, script or no script."""
        monkeypatch.chdir(tmp_path)
        seen = _capture_args(monkeypatch, "_run_build_tool")
        assert _invoke("build", "hello").exit_code == 0
        assert len(seen) == 1
        assert seen[0]["targets"] == ["hello"]


class TestValuesReachTheWork:
    """Options a command declares actually arrive where they are used.

    A command that parses an option and then forgets to pass it on exits 0
    having done the wrong work, which is the failure this whole layer exists to
    prevent and the one no exit code reports.
    """

    def test_jobs_reaches_the_build_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "build.ninja").write_text("")
        seen: list[object] = []
        monkeypatch.setattr("pcons.cli._needs_generation", lambda *a, **k: False)
        monkeypatch.setattr(
            "pcons.cli.run_ninja", lambda *a, **k: seen.append(k.get("jobs")) or 0
        )
        assert _invoke("-B", str(tmp_path), "build", "-j", "3").exit_code == 0
        assert seen == [3]

    def test_the_graph_option_reaches_the_generate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_args(monkeypatch, "_generate", result=(0, None))
        assert _invoke("-B", str(tmp_path), "generate", "--graph=g.dot").exit_code == 0
        assert seen[0]["graph"] == "g.dot"

    def test_no_generator_is_none_rather_than_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """click hands back (), and everything downstream tests truthiness.

        An empty list is falsy too, so this passes either way at the call site
        and fails only where a generator spec is persisted or merged.
        """
        seen = _capture_args(monkeypatch, "_generate", result=(0, None))
        assert _invoke("-B", str(tmp_path), "generate").exit_code == 0
        assert seen[0]["generator"] is None

    def test_a_named_generator_arrives_as_a_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture_args(monkeypatch, "_generate", result=(0, None))
        assert _invoke("-B", str(tmp_path), "-G", "make", "generate").exit_code == 0
        assert seen[0]["generator"] == ["make"]


def _walk(command: click.Command) -> Iterator[click.Command]:
    """Every command in the tree, the groups themselves included.

    A group's callback takes parameters like any other, and `cache` is a group.
    Reads `commands` rather than `list_commands`, which hides the catch-all.
    """
    yield command
    if isinstance(command, click.Group):
        for sub in command.commands.values():
            yield from _walk(sub)


def _named_parameters(command: click.Command) -> set[str]:
    """The callback's own parameters, excluding ctx and any **kw."""
    callback = command.callback
    assert callback is not None, command.name
    params = inspect.signature(callback).parameters
    return {
        name
        for name, p in params.items()
        if name != "ctx" and p.kind is not p.VAR_KEYWORD
    }


class TestSignaturesMatchTheDecorators:
    """Nothing else checks the decorators above a command against the
    parameters below it.

    click calls the callback with the option names as keyword arguments, so a
    parameter spelled differently from the option that feeds it is a TypeError
    the first time that command runs. Several commands are only reached by tests
    that stub the work functions, so that can be a release away.

    Only one direction is checkable. A command takes **kw for what it declares
    and does not consume, and six do, so "every option has a parameter" cannot
    fail for them. "Every parameter is a declared option" is the typo direction
    and is the one that matters.
    """

    def test_every_named_parameter_is_an_option_the_command_declares(self) -> None:
        for command in _walk(cli):
            declared = {
                p.name for p in command.params if p.expose_value and p.name is not None
            }
            assert _named_parameters(command) <= declared, command.name

    def test_every_command_is_walked(self) -> None:
        """The check above is silent about a command it never reaches."""
        names = {command.name for command in _walk(cli)}
        assert {"generate", "build", "clean", "cache", "list", "path", "_default"} <= (
            names
        )


class TestRunningAScriptMoreThanOnce:
    """`run_script` runs more than once per process in watch mode."""

    def test_a_second_run_starts_a_new_project_tree(self, tmp_path):
        """Otherwise the second run's project is adopted by the first's.

        The registry is cleared between runs but the tree behind it was not, so
        `Project.top_level()` still named the project from the run before and
        every path the new one derived was relative to it.
        """
        script = tmp_path / "pcons-build.py"
        script.write_text("from pcons import Project\n\nproject = Project('twice')\n")

        first, _ = run_script(script, tmp_path / "build")
        second, projects = run_script(script, tmp_path / "build")

        assert (first, second) == (0, 0)
        assert projects[-1].is_top_level


class TestAScriptThatRunsItself:
    """A build script may hand over to the CLI from a ``__main__`` guard, and
    one that does not says so when it is run directly.

    All of this is whole-process behaviour: the guard is only true when the
    script is the program, what pcons does about it happens in the exec that
    follows, and what a direct run leaves behind is what an interpreter exit
    did. Nothing here reproduces in-process.
    """

    GUARD = (
        'if __name__ == "__main__":\n'
        "    import sys\n"
        "\n"
        "    import pcons.cli\n"
        "\n"
        "    sys.exit(pcons.cli.main())\n"
    )
    DESCRIBE = 'from pcons import Project\n\nproject = Project("selfrun")\n'

    def _write(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "pcons-build.py"
        script.write_text(body)
        return script

    def _run(
        self, script: Path, *args: str, by_hand: bool = True
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(script)] if by_hand else ["-m", "pcons"]
        return subprocess.run(
            [sys.executable, *argv, *args],
            cwd=script.parent,
            capture_output=True,
            text=True,
            timeout=120,
            env=subprocess_env(),
        )

    def test_the_guard_above_the_description_generates(self, tmp_path):
        script = self._write(tmp_path, self.GUARD + "\n" + self.DESCRIBE)

        result = self._run(script, "generate")

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists(), result.stderr

    def test_the_command_line_reaches_a_script_that_runs_itself(self, tmp_path):
        """The whole point of handing over: argv is parsed before the body."""
        script = self._write(
            tmp_path,
            self.GUARD + "\n"
            "from pcons import Project, get_var, get_variant\n"
            "\n"
            "print(f\"FOO={get_var('FOO', 'unset')} VARIANT={get_variant()}\")\n"
            'project = Project("selfrun")\n',
        )

        result = self._run(script, "FOO=bar", "--variant", "debug", "generate")

        assert result.returncode == 0, result.stderr
        assert "FOO=bar VARIANT=debug" in result.stdout

    def test_the_guard_below_the_description_is_refused(self, tmp_path):
        """By then the description has already run on an unparsed command line."""
        script = self._write(tmp_path, self.DESCRIBE + "\n" + self.GUARD)

        result = self._run(script, "generate")

        assert result.returncode != 0
        assert "before handing over to pcons" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_a_variable_read_above_the_guard_is_refused(self, tmp_path):
        """The read returned its default: PCONS_VARS was not set yet."""
        script = self._write(
            tmp_path,
            "from pcons import get_var\n"
            "\n"
            'debug = get_var("DEBUG", False)\n'
            "\n" + self.GUARD + "\n" + self.DESCRIBE,
        )

        result = self._run(script, "DEBUG=1", "generate")

        assert result.returncode != 0
        assert "before handing over to pcons" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_a_variant_read_above_the_guard_is_refused(self, tmp_path):
        """Same for the variant, which no PCONS_VARIANT had reached yet."""
        script = self._write(
            tmp_path,
            "from pcons import get_variant\n"
            "\n"
            "variant = get_variant()\n"
            "\n" + self.GUARD + "\n" + self.DESCRIBE,
        )

        result = self._run(script, "--variant", "debug", "generate")

        assert result.returncode != 0
        assert "before handing over to pcons" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_a_plain_script_reads_variables_freely(self, tmp_path):
        """The normal shape, and what the refusal must never fire on."""
        script = self._write(
            tmp_path,
            "from pcons import Project, get_var, get_variant\n"
            "\n"
            "print(f\"FOO={get_var('FOO', 'unset')} VARIANT={get_variant()}\")\n"
            'project = Project("plain")\n',
        )

        result = self._run(
            script, "FOO=bar", "--variant", "debug", "generate", by_hand=False
        )

        assert result.returncode == 0, result.stderr
        assert "FOO=bar VARIANT=debug" in result.stdout
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_a_read_in_this_process_does_not_refuse_a_later_run(self, tmp_path):
        """A read is the program's only when the program is the one that made it.

        Anything embedding pcons reads variables from its own code, as this
        file does here, and then drives the CLI. Nothing about that is a
        hand-over, and a run started afterwards must not be refused.
        """
        from pcons import get_var

        get_var("DEBUG", False)
        script = self._write(tmp_path, self.DESCRIBE)

        result = _invoke("-C", str(tmp_path), "generate")

        assert result.exit_code == 0, result.output
        assert script.exists()
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_the_docs_quote_the_refusal_verbatim(self):
        """docs/cli.md shows the message; a rewording there is a wrong doc."""
        docs = Path(__file__).resolve().parents[1] / "docs" / "cli.md"
        section = docs.read_text().split("## A build script that runs itself")[1]
        quoted = [
            block
            for block in section.split("```")
            if block.lstrip().startswith("this build script")
        ]

        assert len(quoted) == 1
        for line in quoted[0].strip().splitlines():
            assert line in cli_module._ACTED_BEFORE_HANDING_OVER

    def test_it_is_refused_even_when_nothing_would_be_generated(self, tmp_path):
        """The refusal belongs to the CLI's entry, not to running the script.

        `pcons build` skips generation when the build files are newer than the
        script, and used to reach ninja without ever looking at the script it
        had already run badly.
        """
        script = self._write(tmp_path, self.DESCRIBE + "\n" + self.GUARD)
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "build.ninja").write_text("# newer than the script\n")

        result = self._run(script, "build")

        assert result.returncode != 0
        assert "before handing over to pcons" in result.stderr

    def test_a_main_guard_does_not_fire_under_pcons(self, tmp_path):
        """`pcons` is the program; the script it runs is not."""
        script = self._write(
            tmp_path,
            self.DESCRIBE + "\n"
            'if __name__ == "__main__":\n'
            '    (project.root_dir / "fired").write_text("x")\n',
        )

        result = self._run(script, "generate", by_hand=False)

        assert result.returncode == 0, result.stderr
        assert not (tmp_path / "fired").exists()

    def test_a_pcons_guard_fires_under_pcons(self, tmp_path):
        """What a script uses to run its description when pcons is driving."""
        script = self._write(
            tmp_path,
            "from pcons import Project\n"
            "\n"
            "def main():\n"
            '    Project("selfrun")\n'
            "\n"
            '\nif __name__ == "__pcons__":\n'
            "    main()\n",
        )

        result = self._run(script, "generate", by_hand=False)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists(), result.stderr

    def test_a_subproject_gets_the_same_name(self, tmp_path):
        """add_subdirectory runs a script too, and a guard must mean one thing."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "pcons-build.py").write_text(
            "from pathlib import Path\n"
            "\n"
            'Path(__file__).parent.joinpath("name.txt").write_text(__name__)\n'
        )
        script = self._write(
            tmp_path,
            "from pcons import Project, add_subdirectory\n"
            "\n"
            'project = Project("parent")\n'
            'add_subdirectory("sub")\n',
        )

        result = self._run(script, "generate", by_hand=False)

        assert result.returncode == 0, result.stderr
        assert (sub / "name.txt").read_text() == "__pcons__"

    def test_a_standalone_subproject_composes_unchanged(self, tmp_path):
        """The shape a subdirectory has when it is also buildable on its own.

        Its hand-over is inert when a parent pulls it in, so the same file
        serves both without the parent re-entering the CLI.
        """
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "pcons-build.py").write_text(
            self.GUARD + "\n"
            "from pathlib import Path\n"
            "\n"
            "from pcons import Project\n"
            "\n"
            'project = Project("sub")\n'
            'Path(__file__).parent.joinpath("ran.txt").write_text("once")\n'
        )
        script = self._write(
            tmp_path,
            "from pcons import Project, add_subdirectory\n"
            "\n"
            'project = Project("parent")\n'
            'add_subdirectory("sub")\n',
        )

        result = self._run(script, "generate", by_hand=False)

        assert result.returncode == 0, result.stderr
        assert (sub / "ran.txt").read_text() == "once"
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_a_direct_run_says_why_nothing_happened(self, tmp_path):
        """Exit 0 is deliberate, not an oversight.

        A direct run writes nothing, so it cannot produce a false green:
        `python pcons-build.py && ninja -C build` fails at ninja whatever this
        returns. The user's problem is not that something wrong succeeded, it
        is having no idea why nothing happened, and the message fixes that.
        """
        script = self._write(tmp_path, self.DESCRIBE)

        result = self._run(script)

        assert result.returncode == 0, result.stderr
        assert "this build script was run directly" in result.stderr
        assert not (tmp_path / "build" / "build.ninja").exists()

    def test_pcons_running_the_script_says_nothing(self, tmp_path):
        script = self._write(tmp_path, self.DESCRIBE)

        result = self._run(script, "-b", "pcons-build.py", "generate", by_hand=False)

        assert "this build script was run directly" not in result.stderr
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "build" / "build.ninja").exists()

    def test_only_the_top_level_project_is_told(self, tmp_path):
        """add_subdirectory builds a project too, and one run is one message."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "pcons-build.py").write_text(
            'from pcons import Project\n\nProject("sub")\n'
        )
        script = self._write(
            tmp_path,
            "from pcons import Project, add_subdirectory\n"
            "\n"
            'project = Project("parent")\n'
            'add_subdirectory("sub")\n',
        )

        result = self._run(script)

        assert result.returncode == 0, result.stderr
        assert result.stderr.count("this build script was run directly") == 1

    def test_an_embedder_reached_through_an_entry_point_is_silent(self, tmp_path):
        """A console script is the program; the project is built elsewhere."""
        (tmp_path / "tool.py").write_text(
            "from pcons import Project\n"
            "\n"
            "\n"
            "def build():\n"
            '    return Project("embedded")\n'
        )
        entry = tmp_path / "entry.py"
        entry.write_text("import tool\n\ntool.build()\n")

        result = self._run(entry)

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_an_embedder_running_its_own_driver_is_told_too(self, tmp_path):
        """Accepted imprecision, written down here rather than found later.

        Nothing at construction time tells `python driver.py` apart from
        `python pcons-build.py`: both are the program, building a top-level
        project with no CLI in the process. The warning is the whole cost:
        the driver runs as it did, and its fix is an entry point.
        """
        driver = tmp_path / "driver.py"
        driver.write_text(
            "from pcons import Project\n"
            "\n"
            'project = Project("embedded")\n'
            'print(f"ROOT={project.root_dir}")\n'
        )

        result = self._run(driver)

        assert result.returncode == 0, result.stderr
        assert "this build script was run directly" in result.stderr
        assert f"ROOT={tmp_path}" in result.stdout


class TestNoProgramToName:
    """``sys.argv[0]`` is the empty string when nothing named a program: an
    interpreter a host application embedded and started itself, and
    ``python -c``. pcons cannot tell what program it is part of, so neither
    the direct-run warning nor the hand-over refusal may fire.

    Both checks compare a file against the program name, and an empty name is
    not a path: ``Path("")`` is ``Path(".")``, which resolves to the working
    directory. That is the one path an unnamed program would be taken for, so
    it is the path each test below hands over.
    """

    DESCRIBE = 'from pcons import Project\n\nproject = Project("unnamed")\n'

    def test_an_unnamed_program_is_no_script(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """running_as_a_program has nothing to compare against, so it says no.

        Answering yes would warn an embedder about a direct run it never made.
        """
        from pcons.core.invocation import running_as_a_program

        monkeypatch.setattr(sys, "argv", [""])
        monkeypatch.chdir(tmp_path)
        script = tmp_path / "pcons-build.py"
        script.write_text(self.DESCRIBE)

        assert running_as_a_program(script) is False
        assert running_as_a_program(tmp_path) is False

    def test_an_unnamed_program_is_refused_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The refusal needs to know which file the program is, and cannot.

        An embedder builds a project of its own and then drives the CLI, which
        is not a hand-over below a description. The project here is declared at
        the working directory, so an unnamed program taken for it would refuse
        a run that has to go through.
        """
        from pcons.core.project import Project
        from pcons.util.source_location import SourceLocation

        monkeypatch.setattr(sys, "argv", [""])
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pcons-build.py").write_text(self.DESCRIBE)
        Project(
            "embedder",
            root_dir=tmp_path,
            defined_at=SourceLocation(str(tmp_path), 1),
        )

        result = _invoke("generate")

        assert result.exit_code == 0, result.output
        assert "before handing over to pcons" not in result.output
        assert (tmp_path / "build" / "build.ninja").exists()


class TestACommandNameThatIsNotTheFirstArgument:
    """click resolves a subcommand from argv[0] and a group stops parsing at
    the first non-option, so anything a user may legitimately write before a
    command name hid it: the name became a target and the command never ran.
    """

    def _resolved(self, *argv: str) -> tuple[str | None, list[str]]:
        """(command name, what is left for it), or ("_default", ...)."""
        ctx = cli.make_context("pcons", [])
        _name, command, rest = cli.resolve_command(ctx, list(argv))
        return (command.name if command is not None else None), rest

    def test_a_variable_no_longer_hides_the_command(self):
        assert self._resolved("FOO=bar", "generate") == ("generate", ["FOO=bar"])

    def test_an_option_stopped_at_a_variable_does_not_hide_it_either(self):
        """The group never parsed `--variant`: `FOO=bar` stopped it first."""
        assert self._resolved("FOO=bar", "--variant", "debug", "generate") == (
            "generate",
            ["FOO=bar", "--variant", "debug"],
        )

    def test_a_target_is_still_a_target(self):
        assert self._resolved("FOO=bar", "hello") == ("_default", ["FOO=bar", "hello"])

    def test_an_option_value_is_not_a_command_name(self):
        """`-C clean` names a directory; the command is still `generate`."""
        assert self._resolved("FOO=bar", "-C", "clean", "generate") == (
            "generate",
            ["FOO=bar", "-C", "clean"],
        )

    def test_a_command_first_is_untouched(self):
        assert self._resolved("generate", "FOO=bar") == ("generate", ["FOO=bar"])
