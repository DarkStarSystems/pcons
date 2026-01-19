#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Example: Using Conan packages with pcons.

This example demonstrates how to use ConanFinder to find Conan packages
and apply their settings (includes, defines, link flags) to your build
environment using the simple env.use() API.

Requirements:
    - Conan 2.x installed (or available via uvx)

Usage:
    uvx pcons              # Generate and build (conan install runs automatically)
    ./build/hello_fmt      # Run the program
"""

import os
from pathlib import Path

from pcons import NinjaGenerator, Project, find_c_toolchain, get_variant
from pcons.configure.config import Configure
from pcons.generators.compile_commands import CompileCommandsGenerator
from pcons.packages.finders import ConanFinder

# =============================================================================
# Configuration
# =============================================================================

VARIANT = get_variant("release")

project_dir = Path(os.environ.get("PCONS_SOURCE_DIR", Path(__file__).parent))
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", project_dir / "build"))

# =============================================================================
# Setup
# =============================================================================

config = Configure(build_dir=build_dir)
toolchain = find_c_toolchain()

if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    toolchain.configure(config)
    config.set("configured", True)
    config.save()

project = Project("conan_example", root_dir=project_dir, build_dir=build_dir)

# =============================================================================
# Find Conan packages
# =============================================================================

print("Finding Conan packages...")

# Create finder - compiler version is auto-detected
conan = ConanFinder(
    config,
    conanfile=project_dir / "conanfile.txt",
    output_folder=build_dir / "conan",
)

# Sync profile with toolchain - this generates the Conan profile file
conan.sync_profile(toolchain, build_type=VARIANT.capitalize())

# Install packages - cmake_layout subfolders are auto-searched
packages = conan.install()

print(f"Found packages: {list(packages.keys())}")

# Get fmt package
fmt_pkg = packages.get("fmt")
if not fmt_pkg:
    raise RuntimeError(
        "fmt package not found - try running:\n"
        "  conan install . --output-folder=build/conan --build=missing"
    )

print(f"fmt version: {fmt_pkg.version}")
print(f"fmt includes: {fmt_pkg.include_dirs}")
print(f"fmt defines: {fmt_pkg.defines}")  # e.g., ['FMT_HEADER_ONLY=1']
print(f"fmt libraries: {fmt_pkg.libraries}")

# =============================================================================
# Environment Setup
# =============================================================================

env = project.Environment(toolchain=toolchain)
env.set_variant(VARIANT)
env.cxx.flags.append("-std=c++17")

# Use C++ linker for C++ programs
env.link.cmd = "clang++"

# =============================================================================
# Apply package settings - use env.use() for simple integration
# =============================================================================

# Apply all package settings (includes, defines, libs, etc.) with one call
env.use(fmt_pkg)

# =============================================================================
# Build target
# =============================================================================
hello = project.Program("hello_fmt", env)
hello.add_sources([project_dir / "src" / "main.cpp"])

project.Default(hello)

# =============================================================================
# Generate build files
# =============================================================================

project.resolve()

NinjaGenerator().generate(project, build_dir)
CompileCommandsGenerator().generate(project, build_dir)

rel_build_dir = build_dir.relative_to(Path.cwd())
print()
print(f"Generated {rel_build_dir / 'build.ninja'}")
print()
print(f"Build: ninja -C {rel_build_dir}")
print(f"Run:   {rel_build_dir / 'hello_fmt'}")
