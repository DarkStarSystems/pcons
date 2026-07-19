#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating multi-level subdirectory builds."""

from pcons import Project, add_subdirectory, get_var

# Create the main project
project = Project("subdirs_example")
env = project.Environment(toolchain=get_var("TOOLCHAIN", "c"))

env.cxx.set_standard("c++20")
if env.toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif env.toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")
    env.link.libs.append("c++")

add_subdirectory("a")
add_subdirectory("b")
add_subdirectory("app")
