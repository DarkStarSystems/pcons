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
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path


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
    target_str = target.replace("\\", "/")
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
    manifest: str | None = None,
) -> None:
    """Copy a directory tree, optionally writing dependency state and a stamp.

    Merges into the destination, leaving files that are already there and
    identical, and anything at the destination the source doesn't have. An
    install directory is often shared — a plugin's config directory, a system
    prefix — and deleting it wholesale would take other people's files with
    it. Pass *replace* to get the destination cleared first.

    Args:
        src: Source directory path.
        dest: Destination directory path.
        depfile: Optional path to write a ninja depfile listing source files.
        stamp: Optional stamp file to touch after copy (for ninja build tracking).
        replace: Delete the destination tree first, rather than merging.
        manifest: Optional JSON path recording files copied by this edge. Files
            previously recorded but absent on a later run are removed.
    """
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.is_dir():
        raise ValueError(f"Source is not a directory: {src}")

    current_files = {
        str(item.relative_to(src_path)).replace("\\", "/")
        for item in src_path.rglob("*")
        if item.is_file()
    }
    if manifest:
        manifest_path = Path(manifest)
        if manifest_path.exists():
            try:
                decoded = json.loads(manifest_path.read_text())
                previous_files = (
                    {item for item in decoded if isinstance(item, str)}
                    if isinstance(decoded, list)
                    else set()
                )
            except (OSError, TypeError, ValueError):
                previous_files = set()
            for relative in previous_files - current_files:
                stale = dest_path / relative
                if stale.is_file() or stale.is_symlink():
                    stale.unlink()
                parent = stale.parent
                while parent != dest_path:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent

    if replace and dest_path.exists():
        shutil.rmtree(dest_path)
    _merge_tree(src_path, dest_path)

    # Write depfile if requested
    if depfile:
        depfile_path = Path(depfile)
        depfile_path.parent.mkdir(parents=True, exist_ok=True)

        # Track the root and every current descendant. Directories are
        # intentional dependencies: their mtimes notice additions/removals,
        # including files in directories created after configuration.
        source_entries = [src_path, *src_path.rglob("*")]
        source_files = [str(item).replace("\\", "/") for item in source_entries]

        # Ninja depfile format, with the stamp file (or dest) as the target
        target_str = _escape_depfile_path((stamp or str(dest_path)).replace("\\", "/"))
        escaped_files = [_escape_depfile_path(f) for f in source_files]
        deps_str = " \\\n  ".join(escaped_files)
        with open(depfile_path, "w", encoding="utf-8") as f:
            f.write(f"{target_str}: \\\n  {deps_str}\n")

    if manifest:
        manifest_path = Path(manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(sorted(current_files)), encoding="utf-8")

    # Touch stamp file if specified
    if stamp:
        stamp_path = Path(stamp)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.touch()


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


def _staged_before(stamp: str | None) -> set[str]:
    """The destination-relative paths the previous overlay run wrote."""
    if not stamp:
        return set()
    stamp_path = Path(stamp)
    if not stamp_path.is_file():
        return set()
    text = stamp_path.read_text(encoding="utf-8")
    return {line for line in text.splitlines() if line}


def _prune_empty(path: Path, stop: Path) -> None:
    """Remove the directories above *path* that it left empty, below *stop*."""
    for parent in path.parents:
        if parent == stop:
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

    *stamp* doubles as the record of what was staged: it holds the list of
    destination-relative paths the previous run produced. That is what lets
    this run delete the copies that no longer win or no longer exist without
    touching anything else the destination holds.

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

    for rel in sorted(_staged_before(stamp) - set(winners)):
        stale = dest_path / rel
        stale.unlink(missing_ok=True)
        _prune_empty(stale, dest_path)

    for rel, source_file in sorted(winners.items()):
        target = dest_path / rel
        if _is_current(source_file, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)

    if depfile:
        _write_depfile(
            depfile, stamp or str(dest_path), walked + sorted(winners.values())
        )

    if stamp:
        stamp_path = Path(stamp)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(
            "".join(f"{rel}\n" for rel in sorted(winners)), encoding="utf-8"
        )


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
        manifest = None
        positional: list[str] = []
        i = 0
        while i < len(args):
            if args[i] == "--depfile" and i + 1 < len(args):
                depfile = args[i + 1]
                i += 2
            elif args[i].startswith("--depfile="):
                depfile = args[i].split("=", 1)[1]
                i += 1
            elif args[i] == "--manifest" and i + 1 < len(args):
                manifest = args[i + 1]
                i += 2
            elif args[i].startswith("--manifest="):
                manifest = args[i].split("=", 1)[1]
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
                "[--depfile FILE] [--manifest FILE] [--stamp FILE] "
                "[--replace] <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copytree(positional[0], positional[1], depfile, stamp, replace, manifest)
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
