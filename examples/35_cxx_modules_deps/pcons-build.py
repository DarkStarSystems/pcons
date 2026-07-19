#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++20 modules example.

Demonstrates:
- Selecting a toolchain via the TOOLCHAIN build variable, else auto-detect
- C++20 named module interface units (.cppm)
- Ninja dyndep for correct module dependency ordering
"""

from pcons import Project, get_var

project = Project("cxx_modules")
env = project.Environment(toolchain=get_var("TOOLCHAIN", "c"))

env.cxx.set_standard("c++20")
if env.toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif env.toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")
    env.link.libs.append("c++")

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
