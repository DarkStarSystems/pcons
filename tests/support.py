# SPDX-License-Identifier: MIT
"""Helpers shared by tests that run pcons in a subprocess."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def subprocess_env(**overrides: str) -> dict[str, str]:
    """Environment for a pcons subprocess, with coverage carried into it.

    Coverage only measures a child process if it starts coverage there too.
    ``coverage`` installs a ``.pth`` hook that does exactly that when
    ``COVERAGE_PROCESS_START`` names a config file. ``COVERAGE_FILE`` has to
    be absolute alongside it: children run with their working directory inside
    the copied example, and a relative data file would be written there and
    never combined.

    This exists so tests can run the real entry points -- ``python
    pcons-build.py``, ``python -m pcons`` -- without trading away the coverage
    that once motivated running them in-process instead. In-process runs
    measured well and tested the wrong thing: they call ``run_script()``, which
    is the CLI's own function, so the atexit path a direct run actually takes
    was never executed.

    Passes through untouched when coverage is not running, so a plain
    ``pytest`` does not pay for subprocess measurement.
    """
    env = {**os.environ, **overrides}

    if _coverage_is_running():
        env["COVERAGE_PROCESS_START"] = str(PYPROJECT)
        env.setdefault("COVERAGE_FILE", str(REPO_ROOT / ".coverage"))

    return env


def _coverage_is_running() -> bool:
    try:
        import coverage
    except ImportError:
        return False
    return coverage.Coverage.current() is not None
