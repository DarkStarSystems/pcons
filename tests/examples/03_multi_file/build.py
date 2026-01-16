#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a multi-file C project.

This example demonstrates:
- Compiling multiple source files
- Using include directories
- Linking multiple object files into an executable
"""

from pathlib import Path
import os

from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain (tries clang, gcc, msvc in order)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("multi_file", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Configure compiler flags and include directories
env.cc.flags = ["-Wall", "-Wextra"]
# Include paths without -I prefix (the prefix is in env.cc.iprefix)
env.cc.includes = [str(include_dir)]

# Compile source files
objs = []
objs += env.cc.Object(build_dir / "math_ops.o", src_dir / "math_ops.c")
objs += env.cc.Object(build_dir / "main.o", src_dir / "main.c")

# Link into executable
env.link.Program(build_dir / "calculator", objs)

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
