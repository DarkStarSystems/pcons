#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducer for missing header-triggered rebuild on regular .cpp in modules mode."""

from pcons import Project, get_var

project = Project("cxx_import_std_header_deps")

env = project.Environment(
    toolchain=get_var("TOOLCHAIN", None) or ["gcc", "llvm", "msvc"]
)

# import std needs C++23 (MSVC has no /std:c++23, so it maps to /std:c++latest).
env.cxx.set_standard("c++23")
if env.toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif env.toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")  # libc++ ships the std module
    env.link.libs.append("c++")

project.Program(
    "hello",
    env,
    sources=["src/Greet.cppm", "src/main.cpp"],
)
