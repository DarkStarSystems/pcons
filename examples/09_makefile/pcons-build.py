#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating Makefile generation.

This example is similar to 02_hello_c but generates a Makefile
instead of Ninja build files.
"""

from pcons import Generator, Project

# =============================================================================
# Build Script
# =============================================================================

# Create project
project = Project("hello_makefile")

# Directories
src_dir = project.root_dir / "src"
env = project.Environment(toolchain="c")

# Create program target using the target-centric API
hello = project.Program("hello", env)
hello.add_sources([src_dir / "hello.c"])
hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Generate a Makefile instead of the default Ninja files
# (still overridable with `pcons -G <name>` / PCONS_GENERATOR)
Generator("make").generate(project)
