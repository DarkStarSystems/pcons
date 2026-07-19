#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a C++20 partition-units example.

Demonstrates:
- A primary module interface in a .cppm file
- A partition INTERFACE unit in a .cpp file (`export module M:P;`)
- An internal partition (implementation) unit in a .cpp file (`module M:P;`)
- A module implementation unit (`module M;`)
- A consumer that imports the module

Pcons identifies module-providing TUs from the P1689R5 scanner output, not
from the file extension, so partition units in `.cpp` files are detected and
get the correct compile flags. On MSVC the internal partition gets
`/internalPartition` (NOT `/interface` — those are mutually exclusive).
"""

from pcons import Project

project = Project("cxx_partitions")
env = project.Environment(toolchain=["llvm", "msvc", "c"])
env.cxx.set_standard("c++20")

project.Program(
    "hello",
    env,
    sources=[
        "src/Calc.cppm",
        "src/Constants.cpp",
        "src/Helpers.cpp",
        "src/Calc_impl.cpp",
        "src/main.cpp",
    ],
)
