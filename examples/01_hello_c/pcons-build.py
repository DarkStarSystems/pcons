#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Automatic resolution and generation
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

project = Project("hello_c", build_dir=build_dir)
env = project.Environment(toolchain=find_c_toolchain())

project.Program("hello", env, sources=[src_dir / "hello.c"])

Generator().generate(project, build_dir)
