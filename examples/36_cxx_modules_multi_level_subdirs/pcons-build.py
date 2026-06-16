#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating multi-level subdirectory builds."""

from pcons import Project, add_subdirectory, find_c_toolchain, get_var

# Create the main project
project = Project("subdirs_example")
env = project.Environment(
    toolchain=(toolchain := find_c_toolchain(prefer=[get_var("TOOLCHAIN", "gcc")]))
)

env.cxx.set_standard("c++20")
if toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")
    env.link.libs.append("c++")

add_subdirectory("a")
add_subdirectory("b")
add_subdirectory("app")
