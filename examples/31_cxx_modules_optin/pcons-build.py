#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a target whose module units live in `.cpp` files.

By default pcons triggers C++ module scanning only when a target has at
least one source whose extension is recognized as a module-interface unit
(`.cppm`, `.ixx`, `.cxxm`, `.c++m`). Real-world projects that put their
primary interface in `.cpp`/`.cc` (e.g. fmtlib's `src/fmt.cc`,
mp-units's `mp-units.cpp`) need an explicit opt-in:

    env.cxx.modules = True

This example exercises that opt-in. Without the flag, pcons would compile
both files as ordinary C++ and the build would fail because main.cpp
imports a module that was never registered.
"""

from pcons import Project
from pcons.toolchains import find_c_toolchain

project = Project("cxx_modules_optin")
toolchain = find_c_toolchain(prefer=["llvm", "msvc"])
env = project.Environment(toolchain=toolchain)
env.cxx.modules = True
env.set_cxx_standard("c++20")

project.Program(
    "hello",
    env,
    sources=["src/Math.cpp", "src/main.cpp"],
)

project.generate()
