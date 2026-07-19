#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bidirectional Swift / C / C++ interop.

Demonstrates:
- Swift importing a C library (`import CStats` via a generated
  module.modulemap — clang_module_map(); a hand-written one shipped in
  the include dir works too)
- C++ calling Swift through the generated <Module>-Swift.h header
  (env.swiftc.interop_header) with C++ interop mode enabled
- Mixed-language linking: swiftc drives the link and brings the Swift
  runtime along
"""

from pcons import Project
from pcons.toolchains.swift import clang_module_map

project = Project("swift_cxx_interop")
env = project.Environment(toolchain="swift")
env.add_toolchain("c")  # C/C++ compilers for the C library and C++ main

env.swiftc.set_cxx_interop("c++17")  # Swift <-> C++ mode
env.swiftc.interop_header = True  # emit <Module>-Swift.h for libraries
env.cxx.set_standard("c++17")

cstats = project.StaticLibrary("cstats", env, sources=["cstats/src/cstats.c"])
cstats.public.include_dirs.append("cstats/include")
# Generate the module map that makes the C library `import`-able from Swift
cstats.public.include_dirs.append(
    clang_module_map(project, "CStats", ["cstats/include/cstats.h"])
)

analyzer = project.StaticLibrary("Analyzer", env, sources=["analyzer/analyzer.swift"])
analyzer.link(cstats)

app = project.Program("interop_demo", env, sources=["src/main.cpp"])
app.link_private(analyzer)
