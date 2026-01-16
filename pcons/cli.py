# SPDX-License-Identifier: MIT
"""Command-line interface for pcons."""

from __future__ import annotations

import argparse
import sys


def cmd_configure(args: argparse.Namespace) -> int:
    """Run the configure phase."""
    print("pcons configure: not yet implemented")
    return 1


def cmd_generate(args: argparse.Namespace) -> int:
    """Run the generate phase."""
    print("pcons generate: not yet implemented")
    return 1


def cmd_build(args: argparse.Namespace) -> int:
    """Run ninja to build targets."""
    print("pcons build: not yet implemented")
    return 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean build artifacts."""
    print("pcons clean: not yet implemented")
    return 1


def main() -> int:
    """Main entry point for the pcons CLI."""
    parser = argparse.ArgumentParser(
        prog="pcons",
        description="A Python-based build system that generates Ninja files.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0-dev")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # pcons configure
    cfg_parser = subparsers.add_parser(
        "configure", help="Run configure phase (tool detection)"
    )
    cfg_parser.add_argument(
        "--build-dir", default="build", help="Build directory (default: build)"
    )
    cfg_parser.set_defaults(func=cmd_configure)

    # pcons generate
    gen_parser = subparsers.add_parser(
        "generate", help="Generate build files from build.py"
    )
    gen_parser.add_argument(
        "--build-dir", default="build", help="Build directory (default: build)"
    )
    gen_parser.set_defaults(func=cmd_generate)

    # pcons build
    build_parser = subparsers.add_parser("build", help="Build targets using ninja")
    build_parser.add_argument("targets", nargs="*", help="Targets to build")
    build_parser.add_argument("-j", "--jobs", type=int, help="Number of parallel jobs")
    build_parser.set_defaults(func=cmd_build)

    # pcons clean
    clean_parser = subparsers.add_parser("clean", help="Clean build artifacts")
    clean_parser.set_defaults(func=cmd_clean)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
