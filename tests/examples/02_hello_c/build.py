#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Using find_c_toolchain() to automatically select a compiler
- Compiling a C source file to an object file
- Linking to create an executable
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

# Find a C toolchain (tries clang, gcc, msvc in order)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("hello_c", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Add warning flags
env.cc.flags = ["-Wall", "-Wextra"]

# Compile: hello.c -> hello.o
obj = env.cc.Object(build_dir / "hello.o", src_dir / "hello.c")

# Link: hello.o -> hello (executable)
env.link.Program(build_dir / "hello", obj)

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
