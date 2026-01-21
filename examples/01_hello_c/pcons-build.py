#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates the target-centric build API:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Using private requirements for compile flags
- Automatic resolution and generation

Works cross-platform: find_c_toolchain() selects the best available
toolchain (clang-cl/MSVC on Windows, clang/gcc on Unix).
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("hello_c", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Create program target using the target-centric API
hello = project.Program("hello", env)
hello.add_sources([src_dir / "hello.c"])

# Add warning flags appropriate for the toolchain
# clang-cl and msvc use MSVC-style flags, others use GCC-style
if toolchain.name in ("msvc", "clang-cl"):
    hello.private.compile_flags.extend(["/W4"])
else:
    hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve targets (computes effective requirements, creates nodes)
project.resolve()

# Generate build file (ninja by default, or --generator from CLI)
generator = Generator()
generator.generate(project, build_dir)

print(f"Generated {build_dir}")
