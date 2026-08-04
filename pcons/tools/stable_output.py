# SPDX-License-Identifier: MIT
"""Make a generator's unchanged outputs look unchanged to the build system.

``restat`` (Ninja) re-checks an output's timestamp after the command runs and
skips downstream work when it didn't move. That only helps if the generator
leaves byte-identical outputs alone — and most don't: they rewrite everything
every time, so one added item in an input list rebuilds the world.

This module wraps such a generator without needing its cooperation. The
command becomes::

    python -m pcons.tools.stable_output --pre  $out && <generator> && \\
    python -m pcons.tools.stable_output --post $out

``--pre`` stashes the existing outputs; ``--post`` restores any output that
came back byte-identical, timestamp included. Used by
``env.Command(..., write_if_different=True)``.

Both halves name their files relative to the directory the build system runs
them in, so they must run in the *same* directory. A command that changes
directory in between (a bare ``cd`` in the middle of the ``&&`` chain) would
otherwise leave ``--post`` looking in the wrong place, restoring nothing and
exiting 0 — the whole feature quietly off, with nothing to see. ``--pre``
therefore records what it did, and ``--post`` fails loudly if it can't find
that record. Use ``env.Command(cwd=...)``, which changes back.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

STASH_DIR = ".pcons-stable"


class StableOutputError(Exception):
    """The stash from ``--pre`` was not where ``--post`` looked for it."""


def _stash_path(output: Path, stash_dir: Path) -> Path:
    """Where *output* is stashed: name plus a hash of its full path.

    The hash keeps same-named outputs from different directories apart.
    """
    digest = hashlib.sha1(str(output).encode()).hexdigest()[:12]
    return stash_dir / f"{output.name}.{digest}"


def _record_path(outputs: list[Path], stash_dir: Path) -> Path:
    """Where the record of one ``--pre`` run lives.

    Named for the output set, so the parallel edges sharing a stash directory
    can't read each other's record.
    """
    digest = hashlib.sha1("\n".join(str(o) for o in outputs).encode()).hexdigest()[:12]
    return stash_dir / f"run.{digest}.json"


def save(outputs: list[Path], stash_dir: Path) -> None:
    """Copy existing outputs aside, preserving their timestamps.

    Also records the run, so ``restore_unchanged`` can tell "nothing needed
    restoring" from "``--post`` ran somewhere ``--pre`` never did".
    """
    stash_dir.mkdir(parents=True, exist_ok=True)
    stashed: list[str] = []
    for output in outputs:
        if output.is_file():
            shutil.copy2(output, _stash_path(output, stash_dir))
            stashed.append(str(output))
    _record_path(outputs, stash_dir).write_text(
        json.dumps({"cwd": str(Path.cwd()), "stashed": stashed})
    )


def restore_unchanged(outputs: list[Path], stash_dir: Path) -> list[Path]:
    """Put back every output that came back byte-identical.

    Returns the outputs that really changed (or are new).

    Raises:
        StableOutputError: if the matching ``save()`` record isn't here, or
            what it stashed has gone missing — the build would silently lose
            the protection otherwise.
    """
    saved = _check_record(outputs, stash_dir)

    changed: list[Path] = []
    lost: list[Path] = []
    for output in outputs:
        stashed = _stash_path(output, stash_dir)
        if not stashed.is_file():
            if str(output) in saved:
                lost.append(output)
            changed.append(output)
            continue
        try:
            if output.is_file() and output.read_bytes() == stashed.read_bytes():
                # copy2 brings the original mtime back with the content, so
                # the build system sees a file that was never touched.
                shutil.copy2(stashed, output)
            else:
                changed.append(output)
        finally:
            stashed.unlink(missing_ok=True)

    if lost:
        raise StableOutputError(
            "--pre stashed these outputs, and they are no longer in "
            f"{stash_dir}:\n  {' '.join(str(o) for o in lost)}\n"
            "They would be treated as changed, rebuilding everything "
            "downstream of them."
        )
    return changed


def _check_record(outputs: list[Path], stash_dir: Path) -> set[str]:
    """Verify this is the directory ``save()`` ran in, and consume the record.

    Returns the outputs ``save()`` reported stashing.
    """
    record = _record_path(outputs, stash_dir)
    cwd = Path.cwd()
    try:
        recorded = json.loads(record.read_text())
        recorded_cwd = Path(recorded["cwd"])
        saved = set(recorded["stashed"])
    except (OSError, ValueError, KeyError):
        raise StableOutputError(
            f"no record of a --pre run for these outputs in {cwd / stash_dir}:\n"
            f"  {' '.join(str(o) for o in outputs)}\n"
            "--pre and --post must run in the same directory. A command that "
            "changes directory (cd ...) between them leaves --post restoring "
            "nothing, so every downstream target rebuilds even when the "
            "generated files are identical. Use env.Command(cwd=...), which "
            "changes back."
        ) from None

    if recorded_cwd != cwd:
        raise StableOutputError(
            f"--pre ran in {recorded_cwd}, --post in {cwd}. The stash found "
            "here belongs to another build; nothing was restored."
        )
    record.unlink(missing_ok=True)
    return saved


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("--pre", "--post"):
        print(
            "usage: python -m pcons.tools.stable_output --pre|--post FILE...",
            file=sys.stderr,
        )
        return 2

    mode, outputs = args[0], [Path(a) for a in args[1:]]
    stash_dir = Path(STASH_DIR)
    if mode == "--pre":
        save(outputs, stash_dir)
    else:
        try:
            restore_unchanged(outputs, stash_dir)
        except StableOutputError as exc:
            print(f"pcons stable_output --post: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
