#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Configure phase: detect tools and set up the build environment."""

import os
import sys
from pathlib import Path

# Add parent pcons to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pcons.configure.config import Configure
from pcons.toolchains import GccToolchain, LlvmToolchain

# Get build directory from environment or use default
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))

# Create configure context
config = Configure(build_dir=build_dir)

# Try to find a C/C++ toolchain
# Prefer LLVM/Clang, fall back to GCC
llvm = LlvmToolchain()
gcc = GccToolchain()

if llvm.configure(config):
    config.set("toolchain", "llvm")
    print("Found LLVM/Clang toolchain")
elif gcc.configure(config):
    config.set("toolchain", "gcc")
    print("Found GCC toolchain")
else:
    print("Error: No C/C++ toolchain found")
    sys.exit(1)

# Save configuration
config.save()
print(f"Configuration saved to {build_dir / 'pcons_config.json'}")
