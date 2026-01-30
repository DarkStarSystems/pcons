#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating separate object compilation.

This example shows:
- Using env.cc.Object() to compile a source file separately
- Passing the resulting object node as a source to Program()

Use case: Compile some sources with different flags (e.g., different
optimization level, or legacy code with warnings disabled).
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

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("object_sources", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Compile helper.c separately with special flags
# For example, compile with -O0 for debugging while rest uses -O2
# Use the toolchain's object suffix for cross-platform compatibility
obj_suffix = toolchain.get_object_suffix()  # .o on Unix, .obj on Windows
helper_obj = env.cc.Object(
    build_dir / f"helper{obj_suffix}",
    src_dir / "helper.c",
)
print(f"helper_obj from env.cc.Object(): {helper_obj}")

# Create program using the pre-compiled object and main.c
# This is the pattern we want to support:
# - main.c gets compiled normally by Program
# - helper.o is already compiled, just link it
prog = project.Program("demo", env)
prog.add_sources(
    [
        src_dir / "main.c",
        helper_obj[0],  # The object node from env.cc.Object()
    ]
)

# Resolve and generate
project.resolve()

# Check what happened
print(f"Program object_nodes: {prog.object_nodes}")
print(f"Program sources: {prog.sources}")

generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
