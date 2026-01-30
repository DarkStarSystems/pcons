#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script testing static library linked into shared library.

This example tests the pattern:
1. Static library (libcore.a) with public includes
2. Shared library (libwrapper.so/dylib) that links the static library
   - Should inherit public includes from core_lib
3. Executable that links the shared library

Expected: core.h is found via public include propagation,
and core_value() from static lib is available in shared lib.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("static_into_shared", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# 1. Create static library from core.c with PUBLIC include directory
#    This should propagate to any target that links core_lib
core_lib = project.StaticLibrary("core", env, sources=[src_dir / "core.c"])
core_lib.public.include_dirs.append(src_dir)  # So dependents can #include "core.h"

# 2. Create shared library from wrapper.c, linking the static library
#    wrapper.c includes "core.h" which should be found via core_lib's public includes
wrapper_lib = project.SharedLibrary("wrapper", env, sources=[src_dir / "wrapper.c"])
wrapper_lib.link(core_lib)

# 3. Create executable that links the shared library
prog = project.Program("demo", env, sources=[src_dir / "main.c"])
prog.link(wrapper_lib)

# Resolve and generate
project.resolve()

# Debug output
print(f"core_lib output_nodes: {core_lib.output_nodes}")
print(f"core_lib public.include_dirs: {list(core_lib.public.include_dirs)}")
print(f"wrapper_lib output_nodes: {wrapper_lib.output_nodes}")
print(f"wrapper_lib dependencies: {wrapper_lib.dependencies}")
print(f"prog output_nodes: {prog.output_nodes}")

generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
