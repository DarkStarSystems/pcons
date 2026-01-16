# SPDX-License-Identifier: MIT
"""Test runner for example projects.

Discovers and runs all example projects in tests/examples/.
Each example is a self-contained project that serves as both
a test and documentation for users.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Try to import tomllib (Python 3.11+) or tomli as fallback
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


EXAMPLES_DIR = Path(__file__).parent / "examples"


def discover_examples() -> list[Path]:
    """Discover all example directories that have a build.py and test.toml."""
    examples = []
    if not EXAMPLES_DIR.exists():
        return examples

    for item in sorted(EXAMPLES_DIR.iterdir()):
        if item.is_dir() and (item / "build.py").exists() and (item / "test.toml").exists():
            examples.append(item)

    return examples


def load_test_config(example_dir: Path) -> dict[str, Any]:
    """Load test.toml configuration."""
    if tomllib is None:
        pytest.skip("tomllib/tomli not available")

    config_file = example_dir / "test.toml"
    with open(config_file, "rb") as f:
        return tomllib.load(f)


def should_skip(config: dict[str, Any]) -> str | None:
    """Check if this test should be skipped. Returns skip reason or None."""
    skip_config = config.get("skip", {})

    # Check platform
    skip_platforms = skip_config.get("platforms", [])
    current_platform = platform.system().lower()
    if current_platform in [p.lower() for p in skip_platforms]:
        return f"Skipped on {current_platform}"

    # Check required tools
    requires = skip_config.get("requires", [])
    for tool in requires:
        if shutil.which(tool) is None:
            return f"Required tool '{tool}' not found"

    return None


def run_example(example_dir: Path, tmp_path: Path) -> None:
    """Run a single example project."""
    config = load_test_config(example_dir)
    test_config = config.get("test", {})

    # Check skip conditions
    skip_reason = should_skip(config)
    if skip_reason:
        pytest.skip(skip_reason)

    # Copy example to temp directory (so we don't pollute the source tree)
    work_dir = tmp_path / example_dir.name
    shutil.copytree(example_dir, work_dir)

    build_dir = work_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Run build.py to generate ninja file
    build_script = work_dir / "build.py"
    result = subprocess.run(
        [sys.executable, str(build_script)],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PCONS_BUILD_DIR": str(build_dir)},
    )

    if result.returncode != 0:
        print(f"build.py stdout:\n{result.stdout}")
        print(f"build.py stderr:\n{result.stderr}")
        pytest.fail(f"build.py failed with code {result.returncode}")

    # Check that build.ninja was generated
    ninja_file = build_dir / "build.ninja"
    if not ninja_file.exists():
        pytest.fail(f"build.ninja not generated in {build_dir}")

    # Run ninja
    if shutil.which("ninja") is None:
        pytest.skip("ninja not available")

    result = subprocess.run(
        ["ninja", "-f", str(ninja_file)],
        cwd=build_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"Ninja stdout:\n{result.stdout}")
        print(f"Ninja stderr:\n{result.stderr}")
        print(f"build.ninja contents:\n{ninja_file.read_text()}")
        pytest.fail(f"ninja failed with code {result.returncode}")

    # Check expected outputs exist
    expected_outputs = test_config.get("expected_outputs", [])
    for output in expected_outputs:
        output_path = work_dir / output
        if not output_path.exists():
            pytest.fail(f"Expected output not found: {output}")

    # Run verification commands
    verify_config = config.get("verify", {})
    verify_commands = verify_config.get("commands", [])

    for cmd_config in verify_commands:
        run_cmd = cmd_config.get("run")
        if not run_cmd:
            continue

        # Resolve command path relative to work_dir
        cmd_path = work_dir / run_cmd
        if cmd_path.exists():
            run_cmd = str(cmd_path)

        result = subprocess.run(
            run_cmd,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Check expected return code
        expected_code = cmd_config.get("expect_returncode", 0)
        if result.returncode != expected_code:
            print(f"Command stdout:\n{result.stdout}")
            print(f"Command stderr:\n{result.stderr}")
            pytest.fail(
                f"Command '{run_cmd}' returned {result.returncode}, "
                f"expected {expected_code}"
            )

        # Check expected stdout
        expect_stdout = cmd_config.get("expect_stdout")
        if expect_stdout is not None:
            if expect_stdout not in result.stdout:
                pytest.fail(
                    f"Expected '{expect_stdout}' in stdout, got:\n{result.stdout}"
                )

        # Check expected file content
        expect_file = cmd_config.get("expect_file")
        expect_content = cmd_config.get("expect_content")
        if expect_file and expect_content:
            file_path = work_dir / expect_file
            if not file_path.exists():
                pytest.fail(f"Expected file not found: {expect_file}")
            actual_content = file_path.read_text()
            if expect_content not in actual_content:
                pytest.fail(
                    f"Expected '{expect_content}' in {expect_file}, "
                    f"got:\n{actual_content}"
                )


# Discover examples and create test parameters
EXAMPLES = discover_examples()


@pytest.mark.parametrize(
    "example_dir",
    EXAMPLES,
    ids=[e.name for e in EXAMPLES],
)
def test_example(example_dir: Path, tmp_path: Path) -> None:
    """Run an example project end-to-end."""
    run_example(example_dir, tmp_path)


# If no examples found, create a placeholder test
if not EXAMPLES:

    def test_no_examples() -> None:
        """Placeholder when no examples are found."""
        pytest.skip("No example projects found in tests/examples/")
