#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example: multi-source program with shared headers.

This example shows:
- Compiling multiple source files into a single program
- Using private.include_dirs for shared headers
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"
include_dir = Path(__file__).parent / "include"

project = Project("transitive_deps", build_dir=build_dir)
env = project.Environment(toolchain=find_c_toolchain())

simulator = project.Program(
    "simulator",
    env,
    sources=[
        src_dir / "math_lib.c",
        src_dir / "physics_lib.c",
        src_dir / "main.c",
    ],
)
simulator.private.include_dirs.append(include_dir)

Generator().generate(project, build_dir)
