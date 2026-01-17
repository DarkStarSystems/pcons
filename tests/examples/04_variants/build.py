#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating debug/release build variants.

This example demonstrates:
- Using set_variant() for debug/release builds
- How variants affect compiler flags and defines
"""

import os
from pathlib import Path

from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Find a C toolchain and create project
toolchain = find_c_toolchain()
project = Project("variants_example", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Apply release variant - sets optimization flags and NDEBUG
env.set_variant("release")

# Add extra flags
env.cc.flags.append("-Wall")

# Compile and link
obj = env.cc.Object(build_dir / "main.o", src_dir / "main.c")
env.link.Program(build_dir / "variant_demo", obj)

# Generate ninja build file
generator = NinjaGenerator()
generator.generate(project, build_dir)

print(f"Generated {build_dir / 'build.ninja'}")
print(f"Variant: {env.variant}")
print(f"CC flags: {env.cc.flags}")
print(f"CC defines: {env.cc.defines}")
