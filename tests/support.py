# SPDX-License-Identifier: MIT
"""Helpers shared by tests that run pcons in a subprocess."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
"""What a Program's output is called, for a test asserting a build tool name."""


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


_NDK_ENV_VARS = ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "ANDROID_NDK")

ANDROID_NDK_FALLBACKS = (Path("/opt/android-ndk"),)
"""Conventional install paths to try when nothing in the environment says.

Distribution paths, not one machine's habit: ``/opt/android-ndk`` is where
Arch Linux's ``android-ndk`` package puts it.
"""

NO_ANDROID_NDK = (
    "no Android NDK found (set ANDROID_NDK_HOME, or install one under ANDROID_HOME/ndk)"
)
"""Skip reason for a test needing an NDK, naming what to set to get one."""


def is_android_ndk(path: Path) -> bool:
    """Whether *path* holds an unpacked Android NDK.

    Looks for a prebuilt LLVM toolchain rather than for the directory itself,
    so an ``ANDROID_NDK_HOME`` left behind by an uninstall skips a test
    instead of failing it somewhere later with a missing compiler.

    Args:
        path: Candidate NDK root.
    """
    return any((path / "toolchains" / "llvm" / "prebuilt").glob("*/bin"))


def _ndk_revision_key(path: Path) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in path.name.split("."))


def find_android_ndk() -> Path | None:
    """An Android NDK to build with, or None if this machine has none.

    The environment comes first, so CI selects the revision it wants and a
    developer can point at any install; then ``ANDROID_HOME/ndk``, highest
    revision first; then the conventional install paths. Never a specific
    revision: the CI runner ships three and a developer machine ships
    whatever it ships.

    Returns:
        The NDK root, or None.
    """
    for var in _NDK_ENV_VARS:
        value = os.environ.get(var)
        if value and is_android_ndk(Path(value)):
            return Path(value)

    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        ndk_dir = Path(sdk) / "ndk"
        if ndk_dir.is_dir():
            for candidate in sorted(
                ndk_dir.iterdir(), key=_ndk_revision_key, reverse=True
            ):
                if is_android_ndk(candidate):
                    return candidate

    for candidate in ANDROID_NDK_FALLBACKS:
        if is_android_ndk(candidate):
            return candidate
    return None
