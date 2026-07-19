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

project = Project("cxx_modules_optin")
env = project.Environment(toolchain=["llvm", "msvc", "c"])
env.cxx.modules = True
env.cxx.set_standard("c++20")

project.Program(
    "hello",
    env,
    sources=["src/Math.cpp", "src/main.cpp"],
)
