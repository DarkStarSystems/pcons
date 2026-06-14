#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducer for missing header-triggered rebuild on regular .cpp in modules mode."""

from pcons import Project, get_var
from pcons.toolchains import find_c_toolchain

project = Project("cxx_import_std_header_deps")

toolchain_override = get_var("TOOLCHAIN", None)
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

# import std needs C++23 (MSVC has no /std:c++23, so it maps to /std:c++latest).
env.set_cxx_standard("c++23")
if toolchain.name == "msvc":
    env.cxx.flags.extend(["/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.append("-stdlib=libc++")  # libc++ ships the std module
    env.link.libs.append("c++")

project.Program(
    "hello",
    env,
    sources=["src/Greet.cppm", "src/main.cpp"],
)

project.generate()
