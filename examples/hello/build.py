#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Build phase: define targets and generate ninja files."""

import os
import sys
from pathlib import Path

# Add parent pcons to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pcons.configure.config import Configure
from pcons.core.project import Project
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains import GccToolchain, LlvmToolchain

# Get directories from environment or use defaults
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", "."))

# Load configuration
config = Configure(build_dir=build_dir)
toolchain_name = config.get("toolchain", "gcc")

# Select toolchain
if toolchain_name == "llvm":
    toolchain = LlvmToolchain()
else:
    toolchain = GccToolchain()

toolchain.configure(config)

# Create project
project = Project("hello", root_dir=source_dir, build_dir=build_dir)

# Create environment with toolchain
env = project.Environment(toolchain=toolchain)

# Build hello program
obj = env.cc.Object("hello.o", "hello.c")
prog = env.link.Program("hello", obj)

# Set as default target
project.Default(prog)

# --- Installer targets (not built by default) ---

# Tarball of source files and headers
src_tarball = project.Tarfile(
    env,
    output=build_dir / "hello-src.tar.gz",
    sources=["hello.c", "hello.h"],
    compression="gzip",
)

# Tarball of the built binary
bin_tarball = project.Tarfile(
    env,
    output=build_dir / "hello-bin.tar.gz",
    sources=prog,  # prog is already a list of FileNodes
    compression="gzip",
)

# Install target: copy tarballs to ./Installers directory
install = project.Install("Installers", [src_tarball, bin_tarball])

# Resolve all targets and generate ninja file
project.resolve()

# Create an alias for convenience: `ninja install`
# Note: Must be after resolve() so Target.output_nodes is populated
project.Alias("install", install)
generator = NinjaGenerator()
generator.generate(project, build_dir)
print(f"Generated {build_dir / 'build.ninja'}")
