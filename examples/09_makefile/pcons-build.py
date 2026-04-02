#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating Makefile generation.

This example is similar to 02_hello_c but generates a Makefile
instead of Ninja build files.
"""

from pcons.core.project import Project
from pcons.generators.makefile import MakefileGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Find a C toolchain (tries clang, gcc, msvc in order)
toolchain = find_c_toolchain()

# Create project with the toolchain
project = Project("hello_makefile")

# Directories
src_dir = project.root_dir / "src"
build_dir = project.build_dir
env = project.Environment(toolchain=toolchain)

# Create program target using the target-centric API
hello = project.Program("hello", env)
hello.add_sources([src_dir / "hello.c"])
hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Resolve targets (computes effective requirements, creates nodes)
project.resolve()

# Generate Makefile (instead of Ninja)
generator = MakefileGenerator()
generator.generate(project)

print(f"Generated {build_dir / 'Makefile'}")
