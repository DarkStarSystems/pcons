# SPDX-License-Identifier: MIT
"""Cross-platform command helpers for pcons build rules.

These helpers are designed to be invoked from ninja build rules using Python.
They handle forward slashes and spaces in paths correctly on all platforms.

Usage in build rules:
    python -m pcons.util.commands copy <src> <dest>
    python -m pcons.util.commands concat <src1> <src2> ... <dest>
    python -m pcons.util.commands copytree [--depfile FILE] [--stamp FILE] <src> <dest>
"""

from __future__ import annotations

import os
import shutil
import sys
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


def _merge_tree(
    src: Path,
    dest: Path,
    _ancestors: frozenset[Path] = frozenset(),
    _root: Path | None = None,
) -> None:
    """Copy *src* over *dest*, skipping files that are already identical.

    Same size and no older than the source is taken as identical, the check
    make and rsync use. It matters at scale: without it one touched file
    re-copies the whole tree, which for a few hundred MB of assets is a
    multi-second stall on any filesystem without copy-on-write.

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
        if target.exists():
            source_stat, target_stat = item.stat(), target.stat()
            if (
                source_stat.st_size == target_stat.st_size
                and source_stat.st_mtime <= target_stat.st_mtime
            ):
                continue
        shutil.copy2(item, target)


def copytree(
    src: str,
    dest: str,
    depfile: str | None = None,
    stamp: str | None = None,
    replace: bool = False,
) -> None:
    """Copy a directory tree, optionally writing a depfile and stamp file.

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
    """
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.is_dir():
        raise ValueError(f"Source is not a directory: {src}")

    if replace and dest_path.exists():
        shutil.rmtree(dest_path)
    _merge_tree(src_path, dest_path)

    # Write depfile if requested
    if depfile:
        depfile_path = Path(depfile)
        depfile_path.parent.mkdir(parents=True, exist_ok=True)

        # Collect all files in the source directory
        source_files: list[str] = []
        for item in src_path.rglob("*"):
            if item.is_file():
                # Use forward slashes for ninja compatibility
                source_files.append(str(item).replace("\\", "/"))

        # Ninja depfile format, with the stamp file (or dest) as the target
        target_str = (stamp or str(dest_path)).replace("\\", "/")
        escaped_files = [_escape_depfile_path(f) for f in source_files]
        deps_str = " \\\n  ".join(escaped_files)
        with open(depfile_path, "w", encoding="utf-8") as f:
            f.write(f"{target_str}: \\\n  {deps_str}\n")

    # Touch stamp file if specified
    if stamp:
        stamp_path = Path(stamp)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.touch()


def main() -> int:
    """Command-line entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python -m pcons.util.commands <command> [args...]", file=sys.stderr
        )
        print("Commands: copy, concat, copytree", file=sys.stderr)
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
                "[--depfile FILE] [--stamp FILE] [--replace] <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copytree(positional[0], positional[1], depfile, stamp, replace)
        return 0

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
