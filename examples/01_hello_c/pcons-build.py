#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Automatic resolution and generation
"""

import os

from pcons import Generator, Project, find_c_toolchain

project = Project("hello_c", build_dir=os.environ.get("PCONS_BUILD_DIR", "build"))
env = project.Environment(toolchain=find_c_toolchain())

project.Program("hello", env, sources=["src/hello.c"])

Generator().generate(project)
