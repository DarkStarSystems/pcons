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

import shutil
import sys
from pathlib import Path


def copy(src: str, dest: str) -> None:
    """Copy a file or directory, creating parent directories as needed."""
    src_path = Path(src)
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        if dest_path.exists():
            shutil.rmtree(dest_path)
        shutil.copytree(src_path, dest_path)
    else:
        shutil.copy2(src, dest)


def concat(sources: list[str], dest: str) -> None:
    """Concatenate multiple files into one."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        for src in sources:
            with open(src, "rb") as f:
                out.write(f.read())


def copytree(
    src: str, dest: str, depfile: str | None = None, stamp: str | None = None
) -> None:
    """Copy a directory tree, optionally writing a depfile and stamp file.

    Args:
        src: Source directory path.
        dest: Destination directory path.
        depfile: Optional path to write a ninja depfile listing source files.
        stamp: Optional stamp file to touch after copy (for ninja build tracking).
    """
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.is_dir():
        raise ValueError(f"Source is not a directory: {src}")

    # Remove destination if it exists to ensure clean copy
    if dest_path.exists():
        shutil.rmtree(dest_path)

    # Copy the directory tree
    shutil.copytree(src_path, dest_path)

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

        # Write ninja depfile format: stamp_file: deps
        # Use the stamp file (or dest) as the "target" for dependency purposes
        target_str = (stamp or str(dest_path)).replace("\\", "/")
        with open(depfile_path, "w") as f:
            f.write(f"{target_str}: \\\n")
            for i, src_file in enumerate(source_files):
                if i < len(source_files) - 1:
                    f.write(f"  {src_file} \\\n")
                else:
                    f.write(f"  {src_file}\n")

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
        if len(sys.argv) != 4:
            print(
                "Usage: python -m pcons.util.commands copy <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copy(sys.argv[2], sys.argv[3])
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
            else:
                positional.append(args[i])
                i += 1

        if len(positional) != 2:
            print(
                "Usage: python -m pcons.util.commands copytree [--depfile FILE] [--stamp FILE] <src> <dest>",
                file=sys.stderr,
            )
            return 1
        copytree(positional[0], positional[1], depfile, stamp)
        return 0

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
