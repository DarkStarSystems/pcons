# SPDX-License-Identifier: MIT
"""Command-line interface for pcons-fetch."""

import argparse
import sys


def main():
    """Main entry point for the pcons-fetch CLI."""
    parser = argparse.ArgumentParser(
        prog="pcons-fetch",
        description="Fetch and build source dependencies for pcons.",
    )
    parser.add_argument(
        "deps_file",
        nargs="?",
        default="deps.toml",
        help="Dependencies file (default: deps.toml)",
    )
    parser.add_argument(
        "--prefix",
        default="deps/install",
        help="Installation prefix (default: deps/install)",
    )
    parser.add_argument(
        "--build-dir",
        default="deps/build",
        help="Build directory (default: deps/build)",
    )
    parser.add_argument(
        "--source-dir",
        default="deps/src",
        help="Source directory (default: deps/src)",
    )

    args = parser.parse_args()

    print("pcons-fetch: not yet implemented")
    print(f"  deps_file: {args.deps_file}")
    print(f"  prefix: {args.prefix}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
