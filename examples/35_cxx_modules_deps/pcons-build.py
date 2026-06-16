#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++20 modules example.

Demonstrates:
- Using find_c_toolchain() to select LLVM/Clang
- C++20 named module interface units (.cppm)
- Ninja dyndep for correct module dependency ordering
"""

from pcons import Project, get_var
from pcons.toolchains import find_c_toolchain

toolchain = find_c_toolchain(prefer=[get_var("TOOLCHAIN", None) or "gcc"])
project = Project("cxx_modules")
env = project.Environment(toolchain=toolchain)

env.cxx.set_standard("c++20")
env.cxx.set_stdlib("libc++")  # no-op unless the toolchain is clang
if toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])

hello = project.Program(
    "hello",
    env,
    sources=[
        "src/mod1.cppm",
        "src/mod2.cppm",
        "src/main.cpp",
    ],
)
hello.private.include_dirs.append("src")

project.generate()
