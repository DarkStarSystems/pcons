#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Archive creation helper for pcons builds.

This script is invoked by Ninja during the build to create tar or zip archives.
It uses Python's built-in tarfile and zipfile modules for cross-platform support.

Usage:
    python -m pcons.util.archive_helper --type tar --compression gzip --output out.tar.gz --base-dir . file1 file2 dir/
    python -m pcons.util.archive_helper --type zip --output out.zip --base-dir . file1 file2 dir/
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path


def create_tarfile(
    output: Path, files: list[Path], compression: str | None, base_dir: Path
) -> None:
    """Create a tar archive.

    Args:
        output: Output archive path.
        files: List of files to include.
        compression: Compression type (None, "gzip", "bz2", "xz").
        base_dir: Base directory for computing archive paths.
    """
    mode = "w"
    if compression == "gzip":
        mode = "w:gz"
    elif compression == "bz2":
        mode = "w:bz2"
    elif compression == "xz":
        mode = "w:xz"

    with tarfile.open(output, mode) as tar:
        for f in files:
            # Compute archive name relative to base_dir
            try:
                arcname = f.relative_to(base_dir)
            except ValueError:
                # File is not under base_dir, use just the filename
                arcname = Path(f.name)
            tar.add(f, arcname=str(arcname))


def create_zipfile(output: Path, files: list[Path], base_dir: Path) -> None:
    """Create a zip archive.

    Args:
        output: Output archive path.
        files: List of files to include.
        base_dir: Base directory for computing archive paths.
    """
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # Compute archive name relative to base_dir
            try:
                arcname = f.relative_to(base_dir)
            except ValueError:
                # File is not under base_dir, use just the filename
                arcname = Path(f.name)
            zf.write(f, arcname=str(arcname))


def expand_directories(paths: list[Path]) -> list[Path]:
    """Expand directories to their contained files.

    Args:
        paths: List of paths (files and/or directories).

    Returns:
        List of file paths with directories expanded.
    """
    result: list[Path] = []
    for p in paths:
        if p.is_dir():
            # Recursively find all files in the directory
            result.extend(f for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            result.append(p)
        # Skip non-existent paths silently (Ninja should have ensured they exist)
    return result


def main() -> int:
    """Main entry point for archive creation."""
    parser = argparse.ArgumentParser(description="Create archive files")
    parser.add_argument(
        "--type",
        choices=["tar", "zip"],
        required=True,
        help="Archive type (tar or zip)",
    )
    parser.add_argument(
        "--compression",
        choices=["gzip", "bz2", "xz"],
        default=None,
        help="Compression type for tar archives",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output archive path",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("."),
        help="Base directory for archive paths (default: current directory)",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Files and directories to include in the archive",
    )

    args = parser.parse_args()

    # Expand directories to file lists
    all_files = expand_directories(args.files)

    if not all_files:
        print(f"Warning: No files to archive for {args.output}", file=sys.stderr)
        # Create empty archive
        if args.type == "tar":
            create_tarfile(args.output, [], args.compression, args.base_dir)
        else:
            create_zipfile(args.output, [], args.base_dir)
        return 0

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Create the archive
    if args.type == "tar":
        create_tarfile(args.output, all_files, args.compression, args.base_dir)
    else:
        create_zipfile(args.output, all_files, args.base_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
