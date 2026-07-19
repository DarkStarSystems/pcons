#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating pkg-config .pc file generation.

This example builds a static library and generates a .pc file so
downstream CMake or pkg-config consumers can find it.
"""

from pcons import Project

project = Project("pkg_config_example")
env = project.Environment(toolchain="c")

# Build a static library with public headers
lib = project.StaticLibrary("mylib", env, sources=["src/mylib.c"])
lib.public.include_dirs.append(project.root_dir / "include")

# Generate mylib.pc in the build directory
pc = project.generate_pc_file(lib, version="1.0.0", description="Example library")

project.Default(lib)
