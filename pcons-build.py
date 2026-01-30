#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Build script for the project."""

import os
from pathlib import Path

from pcons import get_variant
from pcons.configure.config import Configure
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import find_c_toolchain

# Get directories from environment or use defaults
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

# Configuration (auto-cached)
config = Configure(build_dir=build_dir)
if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    # Run configuration checks
    toolchain = find_c_toolchain()
    toolchain.configure(config)
    config.set("configured", True)
    config.save()

# Get build variables
variant = get_variant("release")

# Create project
project = Project("myproject", root_dir=source_dir, build_dir=build_dir)

# Create environment with toolchain
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)
env.set_variant(variant)

# Define your build here
# Example:
# app = project.Program("hello", env, sources=["hello.c"])
# project.Default(app)

# Resolve targets
project.resolve()

# Generate ninja file
generator = NinjaGenerator()
generator.generate(project)
print(f"Generated {build_dir / 'build.ninja'}")
