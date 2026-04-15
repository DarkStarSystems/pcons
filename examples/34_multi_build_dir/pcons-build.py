#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating separate build directories per variant.

Unlike example 03_variants (which uses output_prefix within a single
build directory), this example uses get_variant() to select a variant
and places each in its own build directory:
  build/debug/   — debug build with its own build.ninja
  build/release/ — release build with its own build.ninja

Usage:
  pcons --variant=debug     # generate + build in build/debug/
  pcons --variant=release   # generate + build in build/release/
  VARIANT=debug python pcons-build.py   # direct invocation

Both variants can coexist on disk simultaneously (CMake-style workflow).
This exercises multi-component build_dir paths (e.g. "build/release").
"""

from pathlib import Path

from pcons import Project, find_c_toolchain, get_variant

# =============================================================================
# Build Script
# =============================================================================

# Get the variant (debug by default, overridable via --variant or VARIANT env)
variant = get_variant(default="debug")

# Find a C toolchain (uses platform-appropriate defaults)
toolchain = find_c_toolchain()

src_dir = Path(__file__).parent / "src"

# Create project with variant-specific build directory
project = Project("app", build_dir=f"build/{variant}")
env = project.Environment(toolchain=toolchain)

if toolchain.name in ("msvc", "clang-cl"):
    env.cc.flags.append("/W4")
else:
    env.cc.flags.append("-Wall")

env.set_variant(variant)

prog = project.Program("app", env)
prog.add_sources([src_dir / "main.c"])

project.generate()
print(f"Generated build/{variant}")
