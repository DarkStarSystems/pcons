#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++20 modules example.

Demonstrates:
- Using find_c_toolchain() to select LLVM/Clang
- C++20 named module interface units (.cppm)
- Ninja dyndep for correct module dependency ordering
"""

import os

from pcons import Generator, Project
from pcons.toolchains import find_c_toolchain

project = Project("cxx_modules", build_dir=os.environ.get("PCONS_BUILD_DIR", "build"))
env = project.Environment(toolchain=find_c_toolchain(prefer=["llvm"]))
env.cxx.flags.append("-std=c++20")

project.Program("hello", env, sources=["src/MyMod.cppm", "src/main.cpp"])

Generator().generate(project)
