# SPDX-License-Identifier: MIT
"""Cross-platform command helpers for pcons build rules.

These helpers are designed to be invoked from ninja build rules using Python.
They handle forward slashes and spaces in paths correctly on all platforms.

Usage in build rules:
    python -m pcons.util.commands copy <src> <dest>
    python -m pcons.util.commands concat <src1> <src2> ... <dest>
    python -m pcons.util.commands copytree [--depfile FILE] [--stamp FILE] <src> <dest>
    python -m pcons.util.commands overlay [--depfile FILE] [--stamp FILE]
        [--exclude PATTERN] <dest> <src> [src...]
    python -m pcons.util.commands env NAME=VALUE ... <command> [args...]
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath


def copy(src: str, dest: str, mode: int | None = None) -> None:
    """Copy a file or directory, creating parent directories as needed.

    ``copy2`` carries the source's permissions across, which is usually what
    an install wants. *mode* is for when it isn't — a script that is 0644 in
    the source tree and has to arrive executable.
    """
    src_path = Path(src)
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.copytree(src_path, dest_path)
    else:
        shutil.copy2(src, dest)
    if mode is not None:
        os.chmod(dest_path, mode)


def concat(sources: list[str], dest: str) -> None:
    """Concatenate multiple files into one."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for src in sources:
            with open(src, "rb") as f:
                out.write(f.read())


def _escape_depfile_path(path: str) -> str:
    """Escape spaces in a path for ninja depfile output.

    Ninja depfiles use unescaped whitespace to separate dependencies, so any
    space in a path must be backslash-escaped (mirrors the escaping done in
    pcons/util/latex_deps.py).
    """
    return path.replace(" ", "\\ ")


def _write_depfile(depfile: str, target: str, deps: Sequence[Path]) -> None:
    """Write a ninja depfile naming *deps* as the inputs of *target*."""
    depfile_path = Path(depfile)
    depfile_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = [_escape_depfile_path(str(d).replace("\\", "/")) for d in deps]
    deps_str = " \\\n  ".join(escaped)
    target_str = _escape_depfile_path(target.replace("\\", "/"))
    with open(depfile_path, "w", encoding="utf-8") as f:
        f.write(f"{target_str}: \\\n  {deps_str}\n")


def _is_current(source: Path, target: Path) -> bool:
    """Whether *target* already holds *source*, by size and modification time.

    Same size and no older than the source is what make and rsync take as
    identical. It matters at scale: without it one touched file re-copies the
    whole tree.
    """
    if not target.exists():
        return False
    source_stat, target_stat = source.stat(), target.stat()
    return (
        source_stat.st_size == target_stat.st_size
        and source_stat.st_mtime <= target_stat.st_mtime
    )


def _merge_tree(
    src: Path,
    dest: Path,
    _ancestors: frozenset[Path] = frozenset(),
    _root: Path | None = None,
) -> None:
    """Copy *src* over *dest*, skipping files :func:`_is_current` accepts.

    A symlinked directory is descended into and copied as a real one, which
    is what ``shutil.copytree`` does: a macOS framework is built out of them
    (``Versions/Current``), so stepping over them would install the shape of
    the bundle and none of its contents. Two links to the same directory each
    get a copy — also copytree's behaviour.

    A link is only refused when following it would not terminate: it resolves
    to a directory already on the way down, or to an ancestor of where the
    walk started.
    """
    resolved = src.resolve()
    root = _root or resolved
    # Not on the first call, where root is this directory by definition.
    if _ancestors and (resolved in _ancestors or root.is_relative_to(resolved)):
        return
    ancestors = _ancestors | {resolved}

    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        target = dest / item.name
        if item.is_dir():
            _merge_tree(item, target, ancestors, root)
            continue
        if _is_current(item, target):
            continue
        shutil.copy2(item, target)


def copytree(
    src: str,
    dest: str,
    depfile: str | None = None,
    stamp: str | None = None,
    replace: bool = False,
) -> None:
    """Copy a directory tree, optionally writing dependency state and a stamp.

    Merges into the destination, leaving files that are already there and
    identical, and anything at the destination the source doesn't have. An
    install directory is often shared — a plugin's config directory, a system
    prefix — and deleting it wholesale would take other people's files with
    it. Pass *replace* to get the destination cleared first.

    *stamp* doubles as the record of what was copied, the way it does for
    :func:`overlay`: a file the previous run copied that the source no longer
    holds is removed, along with any directory that leaves empty, and nothing
    else at the destination is touched.

    Args:
        src: Source directory path.
        dest: Destination directory path.
        depfile: Optional ninja depfile, listing the source root and every
            directory and file under it. Directories are deliberate: their
            modification times notice an added or removed entry.
        stamp: Optional stamp file, written with the copied file list.
        replace: Delete the destination tree first, rather than merging.
    """
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.is_dir():
        raise ValueError(f"Source is not a directory: {src}")

    entries = sorted(src_path.rglob("*"))
    current = {
        item.relative_to(src_path).as_posix() for item in entries if item.is_file()
    }

    for rel in sorted(set(_staged_before(stamp)) - current):
        stale = dest_path / rel
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
        _prune_empty(stale, dest_path)

    if replace and dest_path.exists():
        shutil.rmtree(dest_path)
    _merge_tree(src_path, dest_path)

    if depfile:
        _write_depfile(depfile, stamp or str(dest_path), [src_path, *entries])

    if stamp:
        _write_stamp(stamp, dict.fromkeys(current))


def _overlay_excluded(rel_path: Path, patterns: Sequence[str]) -> bool:
    """Whether an overlay entry is filtered out by one of *patterns*.

    A pattern holding no ``/`` matches an entry's name at any depth; one
    holding a ``/`` is anchored at the source root. Matching is case
    sensitive on every platform, so a build description means the same thing
    wherever it runs.

    Args:
        rel_path: File or directory path, relative to its source root.
        patterns: Glob patterns, as passed to ``OverlayDir(exclude=...)``.

    Returns:
        True when the entry is excluded, and with it everything under it.
    """
    text = rel_path.as_posix()
    return any(
        fnmatch.fnmatchcase(text, pattern)
        or ("/" not in pattern and fnmatch.fnmatchcase(rel_path.name, pattern))
        for pattern in patterns
    )


def _overlay_walk(
    root: Path, exclude: Sequence[str]
) -> Iterator[tuple[Path, list[str]]]:
    """Walk *root*, yielding every surviving directory and the files it holds.

    An excluded directory is pruned rather than emptied, so it costs no walk
    and never reaches the depfile — which is what keeps ``exclude=[".git"]``
    from re-running the overlay on every commit.

    Args:
        root: Tree to walk, an existing directory.
        exclude: Glob patterns to drop, matched against paths relative to
            *root*.

    Yields:
        An absolute directory path and its file names, sorted.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel = here.relative_to(root)
        dirnames[:] = sorted(
            name for name in dirnames if not _overlay_excluded(rel / name, exclude)
        )
        yield (
            here,
            sorted(
                name for name in filenames if not _overlay_excluded(rel / name, exclude)
            ),
        )


def _is_dest_relative(rel: str) -> bool:
    """Whether *rel* names a path inside the destination it was staged under.

    Both path flavours are asked, because a stamp is a file on disk: one
    written on another platform can name ``C:/x`` or ``\\\\server\\share``
    where this one sees no anchor at all.
    """
    return bool(rel) and not any(
        pure.anchor or ".." in pure.parts
        for pure in (PurePosixPath(rel), PureWindowsPath(rel))
    )


def _staged_before(stamp: str | None) -> dict[str, str | None]:
    """What the previous overlay run wrote: each destination-relative path
    and the source file that won it, when the stamp recorded one.

    A line naming anything but a path under the destination -- absolute, or
    climbing out with ``..`` -- is dropped with a warning. What the caller
    does with these paths is delete them and prune the directories they
    leave empty, and a stamp is only as trustworthy as the file it was read
    from: one carried over from another tree, half written by an interrupted
    run, or edited by hand. Dropping the line rather than failing keeps a
    bad stamp from wedging the build, and this run rewrites it.
    """
    if not stamp:
        return {}
    stamp_path = Path(stamp)
    if not stamp_path.is_file():
        return {}
    staged: dict[str, str | None] = {}
    for line in stamp_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rel, sep, source = line.partition("\t")
        if not _is_dest_relative(rel):
            print(
                f"pcons: {stamp}: ignoring staged path outside the destination: {rel}",
                file=sys.stderr,
            )
            continue
        staged[rel] = source if sep else None
    return staged


def _write_stamp(stamp: str, staged: Mapping[str, Path | None]) -> None:
    """Write the stamp: each destination-relative path staged and, when
    several trees could have won it, the source file that did, so the next
    run can tell a changed winner apart from an up-to-date copy."""
    stamp_path = Path(stamp)
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(
        "".join(
            f"{rel}\n" if source is None else f"{rel}\t{source}\n"
            for rel, source in sorted(staged.items())
        ),
        encoding="utf-8",
    )


def _prune_empty(path: Path, stop: Path) -> None:
    """Remove the directories above *path* that it left empty, below *stop*.

    A path that is not under *stop* prunes nothing: the walk would never meet
    its sentinel and would climb toward the filesystem root.
    """
    for parent in path.parents:
        if parent == stop or not parent.is_relative_to(stop):
            return
        try:
            parent.rmdir()
        except OSError:
            return


def overlay(
    dest: str,
    sources: Sequence[str],
    exclude: Sequence[str] = (),
    depfile: str | None = None,
    stamp: str | None = None,
) -> None:
    """Merge several source trees into *dest*, later sources winning.

    Each tree's contents land in *dest* keeping their relative paths. When
    two trees hold the same relative path the later one in *sources* wins.

    Membership is decided here, at build time, so a file another build edge
    wrote into a source tree is staged by the build that wrote it, and a file
    added by hand is staged by the build tool alone.

    *stamp* doubles as the record of what was staged: each destination-relative
    path the previous run produced, and the source that won it. That is what
    lets this run delete the copies that no longer win or no longer exist
    without touching anything else the destination holds, and recopy a path
    whose winner changed even when the new winner is the same size and no
    newer than the old copy.

    Args:
        dest: Destination directory, created if missing.
        sources: Source tree roots, in increasing precedence.
        exclude: Glob patterns dropped from every source tree.
        depfile: Optional ninja depfile listing every directory walked and
            every file copied.
        stamp: Optional stamp file, written with the staged file list.

    Raises:
        ValueError: If a source is not a directory.
    """
    dest_path = Path(dest)
    winners: dict[str, Path] = {}
    walked: list[Path] = []

    for source in sources:
        root = Path(source)
        if not root.is_dir():
            raise ValueError(f"Overlay source is not a directory: {source}")
        for directory, filenames in _overlay_walk(root, exclude):
            walked.append(directory)
            for name in filenames:
                item = directory / name
                winners[item.relative_to(root).as_posix()] = item

    dest_path.mkdir(parents=True, exist_ok=True)

    previous = _staged_before(stamp)
    for rel in sorted(set(previous) - set(winners)):
        stale = dest_path / rel
        stale.unlink(missing_ok=True)
        _prune_empty(stale, dest_path)

    for rel, source_file in sorted(winners.items()):
        target = dest_path / rel
        if previous.get(rel) == str(source_file) and _is_current(source_file, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)

    if depfile:
        _write_depfile(
            depfile, stamp or str(dest_path), walked + sorted(winners.values())
        )

    if stamp:
        _write_stamp(stamp, winners)


def run_with_env(args: list[str]) -> int:
    """Run a command with extra environment variables, env(1)-style.

    Leading ``NAME=VALUE`` arguments are set in the environment; the first
    argument that is not an assignment starts the command. Exists because
    Windows has no ``env(1)``: on POSIX pcons writes the real one.
    """
    i = 0
    while i < len(args):
        name, sep, _ = args[i].partition("=")
        if not sep or not name:
            break
        i += 1
    command = args[i:]
    if not command:
        print(
            "Usage: python -m pcons.util.commands env NAME=VALUE ... "
            "<command> [args...]",
            file=sys.stderr,
        )
        return 1
    for assignment in args[:i]:
        name, _, value = assignment.partition("=")
        os.environ[name] = value
    try:
        return subprocess.run(command).returncode
    except OSError as exc:
        print(f"pcons env: {command[0]}: {exc}", file=sys.stderr)
        return 127


def main() -> int:
    """Command-line entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python -m pcons.util.commands <command> [args...]", file=sys.stderr
        )
        print("Commands: copy, concat, copytree, overlay, env", file=sys.stderr)
        return 1

    cmd = sys.argv[1]

    if cmd == "copy":
        args = sys.argv[2:]
        mode = None
        # Anywhere in the list: the generator appends extra flags after the
        # command's own arguments.
        if "--mode" in args:
            i = args.index("--mode")
            mode = int(args[i + 1], 8)
            args = args[:i] + args[i + 2 :]
        if len(args) != 2:
            print(
                "Usage: python -m pcons.util.commands copy [--mode OCTAL] <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copy(args[0], args[1], mode)
        return 0

    elif cmd == "concat":
        if len(sys.argv) < 4:
            print(
                "Usage: python -m pcons.util.commands concat <src1> [src2...] <dest>",
                file=sys.stderr,
            )
            return 1
        concat(sys.argv[2:-1], sys.argv[-1])
        return 0

    elif cmd == "copytree":
        # Parse optional --depfile and --stamp arguments
        args = sys.argv[2:]
        depfile = None
        stamp = None
        replace = False
        positional: list[str] = []
        i = 0
        while i < len(args):
            if args[i] == "--depfile" and i + 1 < len(args):
                depfile = args[i + 1]
                i += 2
            elif args[i].startswith("--depfile="):
                depfile = args[i].split("=", 1)[1]
                i += 1
            elif args[i] == "--stamp" and i + 1 < len(args):
                stamp = args[i + 1]
                i += 2
            elif args[i].startswith("--stamp="):
                stamp = args[i].split("=", 1)[1]
                i += 1
            elif args[i] == "--replace":
                replace = True
                i += 1
            else:
                positional.append(args[i])
                i += 1

        if len(positional) != 2:
            print(
                "Usage: python -m pcons.util.commands copytree "
                "[--depfile FILE] [--stamp FILE] "
                "[--replace] <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copytree(positional[0], positional[1], depfile, stamp, replace)
        return 0

    elif cmd == "overlay":
        args = sys.argv[2:]
        depfile = None
        stamp = None
        exclude: list[str] = []
        positional = []
        i = 0
        while i < len(args):
            if args[i] in ("--depfile", "--stamp", "--exclude") and i + 1 < len(args):
                value = args[i + 1]
                if args[i] == "--depfile":
                    depfile = value
                elif args[i] == "--stamp":
                    stamp = value
                else:
                    exclude.append(value)
                i += 2
            elif args[i].startswith(("--depfile=", "--stamp=", "--exclude=")):
                flag, value = args[i].split("=", 1)
                if flag == "--depfile":
                    depfile = value
                elif flag == "--stamp":
                    stamp = value
                else:
                    exclude.append(value)
                i += 1
            else:
                positional.append(args[i])
                i += 1

        if len(positional) < 2:
            print(
                "Usage: python -m pcons.util.commands overlay "
                "[--depfile FILE] [--stamp FILE] [--exclude PATTERN] "
                "<dest> <src> [src...]",
                file=sys.stderr,
            )
            return 1
        overlay(positional[0], positional[1:], exclude, depfile, stamp)
        return 0

    elif cmd == "env":
        return run_with_env(sys.argv[2:])

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
