# SPDX-License-Identifier: MIT
"""Rebuild-on-change support for ``pcons --watch``.

Watching is deliberately thin. Ninja already knows how to bring the build
description itself up to date (the ``pcons_regen`` edge written by the ninja
generator), and it discovers header dependencies on its own, so all that is
needed here is to notice that *something* under the source tree changed and
run the build again.

Change notification comes from the optional ``watchfiles`` package, which
uses the platform's native mechanism (inotify, FSEvents,
ReadDirectoryChangesW). There is no polling fallback.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import signal
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.errors import PconsError

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Iterable, Iterator, Sequence
    from types import FrameType

INSTALL_HINT = (
    "--watch needs the 'watchfiles' package, which is not installed.\n"
    "It comes with pcons on Linux, macOS and Windows; on other platforms ask "
    "for it explicitly:\n"
    "    pip install 'pcons[watch]'\n"
    "It builds from source where there is no wheel, which needs a Rust toolchain."
)

#: Directories whose contents never justify a rebuild.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        "node_modules",
        ".idea",
        ".vscode",
    }
)

#: Editor scratch and byte-compiled files, by extension.
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".swp", ".swo", ".swx", ".tmp", ".bak"})

#: Exact filenames to ignore: editor droppings, and the bookkeeping a build
#: leaves behind. ``compile_commands.json`` is a symlink pcons maintains at the
#: project root, so reacting to it would rebuild forever; the rest normally sit
#: in the build directory, but an in-source build puts them under the watch.
IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        "4913",
        "compile_commands.json",
        ".ninja_log",
        ".ninja_deps",
        ".ninja_lock",
        "pcons_cache.json",
        "pcons_config.json",
    }
)

#: How soon after a build ends a change must arrive to look self-inflicted.
IMMEDIATE_SECONDS = 1.0

#: Consecutive immediate rebuilds, all blaming one path, that mean a loop.
LOOP_ROUNDS = 6


def ensure_available() -> None:
    """Raise :class:`PconsError` with an install hint if watching is unavailable.

    Called before the first build so the user learns about the missing
    package immediately rather than after a full compile.
    """
    if importlib.util.find_spec("watchfiles") is None:
        raise PconsError(INSTALL_HINT)


def make_ignore(
    excluded_dirs: Sequence[Path] = (),
    excluded_paths: Container[Path] = frozenset(),
) -> Callable[[Path], bool]:
    """Build the predicate deciding which changed paths to disregard.

    Everything under *excluded_dirs* — the build directory above all — is
    ignored: reacting to what the build itself writes would loop forever.
    *excluded_paths* names individual files the same way, for outputs that land
    outside the build directory (a generator writing beside its sources, or an
    in-source build, where excluding the directory would exclude the project).
    It is consulted live, so a caller may keep updating the collection it
    passes as the build description changes.
    """
    excluded = tuple(Path(d).resolve() for d in excluded_dirs)

    def ignored(path: Path) -> bool:
        if path in excluded_paths:
            return True
        # A depfile is written beside its output, named for it plus a suffix,
        # and is never itself an edge output. Asking whether dropping the last
        # suffix names an output identifies one without having to guess at
        # extensions — `.d` is also a perfectly good source extension.
        if path.suffix and path.with_suffix("") in excluded_paths:
            return True
        name = path.name
        if name in IGNORED_NAMES or path.suffix in IGNORED_SUFFIXES:
            return True
        # Editor scratch: emacs lock (.#foo) and autosave (#foo#), backups (foo~),
        # and the temp symlink pcons swaps in at the project root.
        if name.startswith((".#", ".compile_commands.json.")) or name.endswith("~"):
            return True
        if name.startswith("#") and name.endswith("#"):
            return True
        if any(part in IGNORED_DIRS for part in path.parts):
            return True
        return any(path.is_relative_to(base) for base in excluded)

    return ignored


class LoopDetector:
    """Notices a watch that is feeding itself rather than following edits.

    A rebuild loop looks quite unlike fast typing. The next change arrives the
    instant a build ends, because that build wrote it, and the same path comes
    back every round. Demanding both — an immediate trigger *and* a path common
    to every recent round — is what keeps a burst of real saves, which can also
    land back to back, from being mistaken for a loop.
    """

    def __init__(self, rounds: int = LOOP_ROUNDS) -> None:
        self._rounds = rounds
        self._recent: deque[frozenset[Path]] = deque(maxlen=rounds)

    def record(self, triggers: Iterable[Path], gap: float) -> frozenset[Path]:
        """Record a build triggered *gap* seconds after the previous one ended.

        Returns the paths that appear to be retriggering the watch, or an
        empty set while things still look like ordinary editing.
        """
        if gap > IMMEDIATE_SECONDS:
            self._recent.clear()  # a human-scale pause: whatever came before is over
        self._recent.append(frozenset(triggers))
        if len(self._recent) < self._rounds:
            return frozenset()
        return frozenset.intersection(*self._recent)


def watch_and_build(
    build: Callable[[], int],
    roots: Sequence[Path],
    *,
    excluded_dirs: Sequence[Path] = (),
    excluded_paths: Container[Path] = frozenset(),
    changes: Iterable[Iterable[Path]] | None = None,
) -> int:
    """Re-run *build* after every relevant change under *roots*.

    The caller is responsible for the first build; this starts watching from
    whatever state that left behind. A build that fails is reported and
    watching continues, since the next edit is usually the fix. Returns 0 when
    the user stops the watch with Ctrl-C.

    *changes* supplies batches of changed paths, defaulting to ``watchfiles``;
    tests pass their own.
    """
    watched = [Path(r).resolve() for r in roots]
    ignored = make_ignore(excluded_dirs, excluded_paths)
    stop = threading.Event()
    loops = LoopDetector()
    built_at = time.monotonic()

    with _stop_on_interrupt(stop):
        if changes is None:
            changes = _watchfiles_changes(watched, ignored, stop)

        _status(f"Watching {', '.join(str(r) for r in watched)}; Ctrl-C to stop")
        try:
            for batch in changes:
                if stop.is_set():
                    break
                gap = time.monotonic() - built_at
                changed = sorted(p for p in map(Path, batch) if not ignored(p))
                if not changed:
                    continue
                _status(f"Changed: {_describe(changed)}")
                run_build(build)
                built_at = time.monotonic()
                if stop.is_set():  # interrupted during the build
                    break
                culprits = loops.record(changed, gap)
                if culprits:
                    _report_loop(sorted(culprits))
                    return 1
        except KeyboardInterrupt:
            pass

    _status("Stopped watching.")
    return 0


@contextlib.contextmanager
def _stop_on_interrupt(stop: threading.Event) -> Iterator[None]:
    """Make the first Ctrl-C ask the watch loop to finish and stop.

    Raising KeyboardInterrupt out of a build subprocess would leave the
    terminal mid-build, and watchfiles' native backend does not reliably
    surface signals on its own; an event that it polls, and that the loop
    checks around each build, stops promptly either way. The handler is
    removed as it fires, so a second Ctrl-C aborts the hard way.
    """
    if threading.current_thread() is not threading.main_thread():
        yield  # signal handlers can only be installed on the main thread
        return

    def request_stop(signum: int, frame: FrameType | None) -> None:
        stop.set()
        signal.signal(signal.SIGINT, previous)

    previous = signal.signal(signal.SIGINT, request_stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_build(build: Callable[[], int]) -> int:
    """Run one build, reporting how it went. Errors are reported, not raised."""
    start = time.monotonic()
    _status(f"--- Building at {time.strftime('%H:%M:%S')} ---")
    try:
        code = build()
    except PconsError as e:
        _status(f"Build failed: {e}")
        return 1
    except Exception:  # noqa: BLE001 - keep watching; the next edit is usually the fix
        traceback.print_exc()
        return 1
    elapsed = time.monotonic() - start
    if code == 0:
        _status(f"Build succeeded in {elapsed:.1f}s")
    else:
        _status(f"Build failed (exit {code}) after {elapsed:.1f}s")
    return code


def _watchfiles_changes(
    roots: Sequence[Path], ignored: Callable[[Path], bool], stop: threading.Event
) -> Iterator[set[Path]]:
    """Yield batches of changed paths from watchfiles' native watcher.

    watchfiles coalesces bursts for us: a batch arrives once changes have
    stopped arriving for a moment, so one "save all" is one rebuild.
    """
    try:
        from watchfiles import watch
    except ImportError as e:  # pragma: no cover - ensure_available() runs first
        raise PconsError(INSTALL_HINT) from e

    for batch in watch(
        *roots,
        watch_filter=lambda _change, path: not ignored(Path(path)),
        stop_event=stop,
    ):
        yield {Path(path) for _change, path in batch}


def _report_loop(culprits: Sequence[Path]) -> None:
    """Explain a self-feeding watch, and name what keeps setting it off."""
    _status(
        f"\nStopping: this looks like a rebuild loop. The last {LOOP_ROUNDS} builds "
        "each started the moment the previous one finished, every one of them "
        "triggered by:"
    )
    for path in culprits[:5]:
        _status(f"    {_display(path)}")
    _status(
        "The build itself writes those, so each build asks for the next. Declare "
        "them as outputs of the command that writes them, or send them to the "
        "build directory, and they will stop being watched."
    )


def _describe(paths: Sequence[Path], limit: int = 3) -> str:
    """Name the changed files, briefly."""
    shown = ", ".join(_display(p) for p in paths[:limit])
    extra = len(paths) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _display(path: Path) -> str:
    """Spell a path relative to the working directory when that is shorter."""
    try:
        relative = os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return str(path)
    return relative if len(relative) < len(str(path)) else str(path)


def _status(message: str) -> None:
    """Report watch progress on stderr, leaving stdout to the build tool."""
    print(message, file=sys.stderr, flush=True)
