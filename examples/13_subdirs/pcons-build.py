#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating subdirectory builds.

This example shows how to organize a project with subdirectories,
where each subdir can be built standalone OR as part of the main build.

Structure:
  13_subdirs/
    pcons-build.py      <- This file (main build)
    libfoo/
      pcons-build.py    <- Standalone: builds just libfoo
      src/foo.c
      include/foo.h
    app/
      pcons-build.py    <- Standalone: builds app + libfoo
      src/main.c

Usage:
  # Build everything from top level
  python pcons-build.py && ninja -C build

  # Or build just libfoo standalone
  cd libfoo && python pcons-build.py && ninja -C build

  # Or build app (which pulls in libfoo)
  cd app && python pcons-build.py && ninja -C build
"""

import os
import runpy
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

this_dir = Path(__file__).parent
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))

# Create the main project
project = Project("subdirs_example", build_dir=build_dir)
toolchain = find_c_toolchain()

# Load libfoo's build script using runpy (simpler than importlib.util)
libfoo_module = runpy.run_path(str(this_dir / "libfoo" / "pcons-build.py"))
libfoo = libfoo_module["build_libfoo"](project, build_dir)

# Build the app, linking to libfoo (gets includes automatically)
app_src_dir = this_dir / "app" / "src"
env = project.Environment(toolchain=toolchain)

app = project.Program("subdirs_demo", env)
app.add_sources([app_src_dir / "main.c"])
app.link(libfoo)  # Gets libfoo's public.include_dirs automatically

# Resolve and generate the ninja file
project.resolve()
Generator().generate(project)
print(f"Generated {build_dir}")
