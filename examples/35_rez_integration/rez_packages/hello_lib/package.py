# SPDX-License-Identifier: MIT
"""Test rez package: a tiny C++ static library.

Built with rez's built-in cmake build_system. Installs include/ and lib/
into the rez install root, so that REZ_HELLO_LIB_ROOT/include and
REZ_HELLO_LIB_ROOT/lib are picked up by pcons's RezFinder via the
convention-based scan.
"""

name = "hello_lib"
version = "0.1.0"

authors = ["pcons"]
description = "Test rez package for pcons integration"

build_system = "cmake"


def commands():
    # rez automatically exports REZ_HELLO_LIB_ROOT pointing at the install
    # root; that's all pcons needs. Nothing extra to do here.
    pass
