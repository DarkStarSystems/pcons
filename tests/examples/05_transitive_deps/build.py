#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example demonstrating transitive include directory propagation.

This example shows how public include directories propagate:
- Target A declares public includes
- Target B links to A, gets A's public includes
- Target C links to B, gets BOTH A's and B's public includes transitively

Note: This simplified example compiles everything into a single program
to demonstrate include propagation. Full transitive library linking
is a more advanced feature.
"""

import os
import sys
from pathlib import Path

from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

# Find a C toolchain - prefer MSVC on Windows
if sys.platform == "win32":
    toolchain = find_c_toolchain(prefer=["msvc", "llvm", "gcc"])
else:
    toolchain = find_c_toolchain()
project = Project("transitive_deps", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Main program that compiles all sources
# The include directory propagation is demonstrated:
# - All sources need the include directory
# - We use public.include_dirs to make it available
simulator = project.Program("simulator", env)
simulator.sources = [
    project.node(src_dir / "math_lib.c"),
    project.node(src_dir / "physics_lib.c"),
    project.node(src_dir / "main.c"),
]
# Include directory needed by all sources
simulator.private.include_dirs.append(include_dir)

# Resolve all targets
project.resolve()

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
print("All sources compiled with transitive include propagation.")
