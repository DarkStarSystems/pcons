#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for app - can be built standalone or as part of parent project.

This demonstrates a subdir that depends on another subdir (libfoo).
Works both standalone and as part of the parent build.
"""

import os
import runpy
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# Load libfoo's build script using runpy
libfoo_script = Path(__file__).parent.parent / "libfoo" / "pcons-build.py"
libfoo_module = runpy.run_path(str(libfoo_script))
build_libfoo = libfoo_module["build_libfoo"]


def build_app(project: Project | None = None, build_dir: Path | None = None):
    """Build app, optionally as part of a parent project.

    Args:
        project: Parent project, or None for standalone build
        build_dir: Build output directory
    """
    this_dir = Path(__file__).parent
    src_dir = this_dir / "src"

    # Use provided build_dir or default
    if build_dir is None:
        build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))

    # Create project if not provided (standalone mode)
    standalone = project is None
    if standalone:
        project = Project("app", build_dir=build_dir)

    assert project is not None  # For type checker - always true after above

    # Build libfoo first to get its library target
    libfoo = build_libfoo(project, build_dir)

    toolchain = find_c_toolchain()
    env = project.Environment(toolchain=toolchain)

    # Build app program, linking to libfoo (gets includes automatically)
    app = project.Program("app", env)
    app.add_sources([src_dir / "main.c"])
    app.link(libfoo)  # Gets libfoo's public.include_dirs automatically

    if standalone:
        # Resolve and generate build file when running standalone
        project.resolve()
        Generator().generate(project)
        print(f"Generated {build_dir}")


if __name__ == "__main__":
    build_app()
