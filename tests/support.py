# SPDX-License-Identifier: MIT
"""Helpers shared by tests across directories."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from pcons.core.project import Project

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
"""What a Program's output is called, for a test asserting a build tool name."""

TOOL_ENV_VARS = ("CC", "CXX", "FC", "AR", "SWIFTC", "CUDACXX", "RC")
"""The conventional tool-selection variables, which pcons honors."""

USER_TOOL_ENV = {var: os.environ[var] for var in TOOL_ENV_VARS if os.environ.get(var)}
"""The tool choices pytest was started with (``FC=flang pytest ...``),
captured at import, before conftest hides them from in-process tests.
Example tests run pcons as a user would, so they hand these back."""


def qt_runtime_env() -> dict[str, str]:
    """Environment for running a program linked against Qt.

    Windows has no rpath: a Qt executable finds its DLLs on PATH, so the
    discovered Qt tool directories go first. Elsewhere the environment is
    returned as it is.
    """
    env = dict(os.environ)
    if sys.platform == "win32":
        from pcons.toolchains.qt.toolchain import _locate_tool_dirs

        qt_dirs = os.pathsep.join(str(d) for d in _locate_tool_dirs())
        if qt_dirs:
            env["PATH"] = qt_dirs + os.pathsep + env.get("PATH", "")
    return env


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


def refresh_dependency_metadata(*paths: Path) -> None:
    """List each directory so ninja sees its current modification time.

    NTFS keeps a directory's modification time in the parent's index entry,
    which ninja reads and which is refreshed when a handle to the directory
    is opened and closed, not when a file is written into it. A build
    started right after a write can therefore see the old time; listing the
    directory refreshes it at once, on every Python and Windows version,
    without changing a timestamp. Callers pass the directories that gained
    or lost an entry and still exist.
    """
    for path in paths:
        os.listdir(path)


def object_of(project: Project, target: str, source: str) -> str:
    """The object file *target* compiles *source* to, named as the toolchain names it.

    The suffix is ``.o`` or ``.obj`` depending on the detected toolchain, so no
    test may spell it out.
    """
    nodes = [
        node
        for node in project.get_target(target).intermediate_nodes
        if node.path.name.startswith(source)
    ]
    assert len(nodes) == 1, [node.path.name for node in nodes]
    return nodes[0].path.name


def building_for(triple: str) -> AbstractContextManager[object]:
    """Pin what every environment builds for, leaving the host alone.

    Naming, import libraries and link groups read ``env.target``, and only
    that, so this is a cross build as far as they can tell.
    """
    from unittest.mock import PropertyMock, patch

    from pcons.core.environment import Environment
    from pcons.toolchains.presets import target_platform_for_triple

    return patch.object(
        Environment,
        "target",
        new_callable=PropertyMock,
        return_value=target_platform_for_triple(triple),
    )


def apply_variant(toolchain: Any, env: Any, variant: str, **kwargs: Any) -> None:
    """Apply *toolchain*'s realization of *variant* to a hand-built *env*,
    as the environment does for its own toolchains."""
    env.apply_from(toolchain, toolchain.make_variant_preset(variant, **kwargs))


def apply_target_arch(toolchain: Any, env: Any, arch: str) -> bool:
    """Apply *toolchain*'s realization of *arch*; whether it had one."""
    preset = toolchain.make_arch_preset(arch)
    if preset is None:
        return False
    env.apply_from(toolchain, preset)
    return True


def apply_cross_preset(toolchain: Any, env: Any, cross: Any) -> None:
    """Apply *toolchain*'s realization of cross target *cross*."""
    env.apply_from(toolchain, toolchain.make_target_preset(cross))


def apply_feature(toolchain: Any, env: Any, name: str) -> None:
    """Apply *toolchain*'s realization of feature preset *name*."""
    preset = toolchain.make_feature_preset(name, env.target)
    assert preset is not None, name
    env.apply_from(toolchain, preset)


def gone(pid: int, deadline: float) -> bool:
    """Whether process *pid* has exited, waiting up to *deadline* seconds.

    Windows waits on a process handle: ``os.kill`` there terminates the
    process instead of probing it.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(handle, int(deadline * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)

    end = time.monotonic() + deadline
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if zombie(pid):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def zombie(pid: int) -> bool:
    """Whether process *pid* exited but was never reaped.

    An orphan is reaped by init, but a container may run without one, and
    there a killed orphan stays a zombie. Linux only: elsewhere ``False``.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    # The state follows the command name, which is in parentheses.
    return stat.rpartition(")")[2].split()[0] == "Z"


_SPAWNER_PREFIX = """\
import os, subprocess, sys, time

def record(pid):
    tmp = {pid_file!r} + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(pid))
    os.replace(tmp, {pid_file!r})

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
"""

_SPAWNER_BODIES = {
    "direct": """\
print("partial", flush=True)
record(subprocess.Popen(SLEEPER).pid)
time.sleep(60)
""",
    "orphan": """\
print("partial", flush=True)
middle = "record(subprocess.Popen(SLEEPER).pid)"
subprocess.run([sys.executable, "-c", PREFIX + middle], check=True)
time.sleep(60)
""",
    "exited": """\
print("partial", flush=True)
record(subprocess.Popen(SLEEPER).pid)
""",
    "escaped": """\
print("partial", flush=True)
record(subprocess.Popen(SLEEPER, start_new_session=True).pid)
time.sleep(60)
""",
    "daemon": """\
quiet = subprocess.DEVNULL
record(subprocess.Popen(SLEEPER, stdin=quiet, stdout=quiet, stderr=quiet).pid)
""",
}


def write_spawner(script: Path, pid_file: Path, how: str) -> Path:
    """Write a Python script that starts a process sleeping 60 s.

    The script records that process's pid in *pid_file*, atomically.

    Args:
        script: Where to write the script.
        pid_file: Where the script records the pid.
        how: ``direct``: the script prints ``partial``, starts the sleeper
            with its stdout and stderr, and sleeps 60 s. ``orphan``: the same,
            but a middle process starts the sleeper and exits first.
            ``exited``: the same as ``direct``, but the script exits at once.
            ``escaped``: the same as ``direct``, but the sleeper leads a new
            session (POSIX only).
            ``daemon``: the sleeper gets no stdio and the script exits 0.
            Each script prints ``partial`` before the pid is recorded.

    Returns:
        *script*.
    """
    prefix = _SPAWNER_PREFIX.format(pid_file=str(pid_file))
    script.write_text(f"{prefix}PREFIX = {prefix!r}\n{_SPAWNER_BODIES[how]}")
    return script


def read_pid(pid_file: Path, deadline: float) -> int:
    """The pid recorded in *pid_file*, waiting up to *deadline* seconds for it."""
    end = time.monotonic() + deadline
    while not pid_file.exists():
        assert time.monotonic() < end, f"{pid_file} never appeared"
        time.sleep(0.05)
    return int(pid_file.read_text())


def kill_pid(pid: int) -> None:
    """Kill process *pid* if it still runs, for test cleanup.

    A pid that is gone is left alone: Windows reuses pids fast, and
    ``os.kill`` there terminates whatever process holds it now.
    """
    if gone(pid, 0):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
