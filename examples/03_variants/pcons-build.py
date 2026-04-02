#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating debug/release build variants.

This example demonstrates:
- Using env.clone() to create multiple environments
- Building both debug and release variants in one project
- How variants affect compiler flags and defines
- Organizing outputs into variant-specific directories
"""

from pcons import Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()
project = Project("variants_example")

# Directories
src_dir = project.root_dir / "src"
build_dir = project.build_dir

# Create base environment with common settings
base_env = project.Environment(toolchain=toolchain)
if toolchain.name in ("msvc", "clang-cl"):
    base_env.cc.flags.append("/W4")
else:
    base_env.cc.flags.append("-Wall")

# Build both debug and release variants
for variant in ["debug", "release"]:
    env = base_env.clone()
    env.set_variant(variant)  # Sets appropriate flags for each variant

    prog = project.Program(f"variant_demo_{variant}", env)
    prog.output_name = f"{variant}/" + toolchain.get_program_name("variant_demo")
    prog.add_sources([src_dir / "main.c"])

project.generate()
print(f"Generated {build_dir}")
