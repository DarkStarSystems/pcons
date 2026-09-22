# SPDX-License-Identifier: MIT
"""Build script demonstrating subdirectory builds.

This example shows how to organize a project with subdirectories. A subdir
whose dependencies all sit below it builds standalone as well as here; one
that reaches sideways, as app does, builds as part of this project and is
handed what it needs with `imports=`.

Structure:
  13_subdirs/
    pcons-build.py      <- This file (main build)
    libfoo/
      pcons-build.py    <- Standalone: builds libfoo, and libbar below it
      src/foo.c
      include/foo.h
    app/
      pcons-build.py    <- Links libfoo, which sits beside it, not below
      src/main.c

Usage:
  # Build everything from top level
  pcons

  # Or build just libfoo standalone
  cd libfoo && pcons
"""

from pathlib import Path

from pcons import Project, add_subdirectory

this_dir = Path(__file__).parent

# Create the main project
project = Project("subdirs_example")
env = project.Environment(toolchain="c")

# add_subdirectory() returns a SimpleNamespace of all module-level names
# defined in the subdir's pcons-build.py.  libfoo/pcons-build.py assigns
# `libfoo = project.StaticLibrary(...)` at module scope, so it is exported.
libfoo_ns = add_subdirectory("libfoo")

# The return value is the way up; `imports=` is the way down. app sits beside
# libfoo rather than below it, so it has no way of its own to reach that
# target: this script hands it over, and app reads it as `project.imports`.
add_subdirectory("app", imports={"libfoo": libfoo_ns.libfoo})
