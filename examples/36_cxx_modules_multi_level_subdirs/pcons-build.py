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
env.cxx.set_stdlib("libc++")  # no-op unless the toolchain is clang
if toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])

add_subdirectory("a")
add_subdirectory("b")
add_subdirectory("app")
