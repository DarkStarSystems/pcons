#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example demonstrating a multi-source program with shared headers.

This example shows:
- Compiling multiple source files into a single program
- Using private.include_dirs for shared headers
- All sources can include headers from the include directory

For transitive dependencies between libraries, see 06_multi_library.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()
project = Project("transitive_deps", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Main program with multiple source files
simulator = project.Program("simulator", env)
simulator.add_sources(
    [
        src_dir / "math_lib.c",
        src_dir / "physics_lib.c",
        src_dir / "main.c",
    ]
)
# All sources need access to headers in include/
simulator.private.include_dirs.append(include_dir)

# Resolve all targets
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project, build_dir)

print(f"Generated {build_dir}")
