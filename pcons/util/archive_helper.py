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
from typing import Literal


def create_tarfile(
    output: Path, files: list[Path], compression: str | None, base_dir: Path
) -> None:
    """Create a tar archive with optional compression (gzip/bz2/xz);
    archive paths are relative to base_dir."""
    compression_modes: dict[str, Literal["w:gz", "w:bz2", "w:xz"]] = {
        "gzip": "w:gz",
        "bz2": "w:bz2",
        "xz": "w:xz",
    }
    mode: Literal["w", "w:gz", "w:bz2", "w:xz"] = (
        compression_modes.get(compression, "w") if compression else "w"
    )

    with tarfile.open(output, mode=mode) as tar:
        for f in files:
            try:
                arcname = f.relative_to(base_dir)
            except ValueError:
                # Not under base_dir: use just the filename
                arcname = Path(f.name)
            tar.add(f, arcname=str(arcname))


def create_zipfile(output: Path, files: list[Path], base_dir: Path) -> None:
    """Create a zip archive; archive paths are relative to base_dir."""
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            try:
                arcname = f.relative_to(base_dir)
            except ValueError:
                # Not under base_dir: use just the filename
                arcname = Path(f.name)
            zf.write(f, arcname=str(arcname))


def expand_directories(paths: list[Path]) -> list[Path]:
    """Expand directories recursively to their contained files."""
    result: list[Path] = []
    for p in paths:
        if p.is_dir():
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

    all_files = expand_directories(args.files)

    if not all_files:
        print(f"Warning: No files to archive for {args.output}", file=sys.stderr)
        # Create empty archive
        if args.type == "tar":
            create_tarfile(args.output, [], args.compression, args.base_dir)
        else:
            create_zipfile(args.output, [], args.base_dir)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)

    if args.type == "tar":
        create_tarfile(args.output, all_files, args.compression, args.base_dir)
    else:
        create_zipfile(args.output, all_files, args.base_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
