#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++20 modules example.

Demonstrates:
- Selecting a toolchain by preference list: LLVM first, else auto-detect
- C++20 named module interface units (.cppm)
- Ninja dyndep for correct module dependency ordering
"""

from pcons import Project

project = Project("cxx_modules")
env = project.Environment(toolchain=["llvm", "c"])
env.cxx.set_standard("c++20")

project.Program("hello", env, sources=["src/MyMod.cppm", "src/main.cpp"])
