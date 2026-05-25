#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproducer for missing header-triggered rebuild on regular .cpp in modules mode."""

from pcons import Project, get_var
from pcons.toolchains import find_c_toolchain

project = Project("cxx_import_std_header_deps")

toolchain_override = get_var("TOOLCHAIN")
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

if toolchain.name == "msvc":
    env.cxx.flags.extend(["/std:c++latest", "/EHsc", "/permissive-"])
elif toolchain.name == "llvm":
    env.cxx.flags.extend(["-std=c++23", "-stdlib=libc++"])
    env.link.libs.append("c++")
else:
    env.cxx.flags.append("-std=c++23")

project.Program(
    "hello",
    env,
    sources=["src/Greet.cppm", "src/main.cpp"],
)

project.generate()
