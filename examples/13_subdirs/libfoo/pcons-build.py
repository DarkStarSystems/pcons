#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for libfoo - can be built standalone or as part of parent project.

This demonstrates a subdir that works both:
- Standalone: `cd libfoo && python pcons-build.py`
- As subdir: called from parent pcons-build.py
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain
from pcons.core.target import Target


def build_libfoo(
    project: Project | None = None, build_dir: Path | None = None
) -> Target:
    """Build libfoo, optionally as part of a parent project.

    Args:
        project: Parent project, or None for standalone build
        build_dir: Build output directory

    Returns:
        The libfoo static library target (with public.include_dirs set)
    """
    this_dir = Path(__file__).parent
    src_dir = this_dir / "src"
    include_dir = this_dir / "include"

    # Use provided build_dir or default
    if build_dir is None:
        build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))

    # Create project if not provided (standalone mode)
    standalone = project is None
    if standalone:
        project = Project("libfoo", build_dir=build_dir)

    assert project is not None  # For type checker - always true after above

    toolchain = find_c_toolchain()
    env = project.Environment(toolchain=toolchain)

    # Build static library with public include directory
    libfoo = project.StaticLibrary("foo", env)
    libfoo.add_sources([src_dir / "foo.c"])
    libfoo.public.include_dirs.append(include_dir)

    if standalone:
        # Resolve and generate build file when running standalone
        project.resolve()
        Generator().generate(project)
        print(f"Generated {build_dir}")

    return libfoo


if __name__ == "__main__":
    build_libfoo()
