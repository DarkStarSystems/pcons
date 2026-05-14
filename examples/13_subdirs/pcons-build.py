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

from pathlib import Path

from pcons import Project, add_subdirectory, find_c_toolchain

this_dir = Path(__file__).parent

# Create the main project
project = Project("subdirs_example")
env = project.Environment(toolchain=find_c_toolchain())

# add libfoo and app subdirectories
add_subdirectory("libfoo")
add_subdirectory("app")
