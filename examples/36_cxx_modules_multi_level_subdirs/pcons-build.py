#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating multi-level subdirectory builds."""

from pcons import Project, add_subdirectory, find_c_toolchain, get_var

# Create the main project
project = Project("subdirs_example")
env = project.Environment(
    toolchain=(toolchain := find_c_toolchain(prefer=[get_var("TOOLCHAIN", "gcc")]))
)

if toolchain.name == "msvc":
    env.cxx.flags.extend(["/std:c++latest", "/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.extend(["-std=c++20", "-stdlib=libc++"])
    env.link.libs.append("c++")
else:
    env.cxx.flags.append("-std=c++20")

add_subdirectory("a")
add_subdirectory("b")
add_subdirectory("app")
