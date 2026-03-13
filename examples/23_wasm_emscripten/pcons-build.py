#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple Emscripten WebAssembly program.

This example demonstrates:
- Using find_emscripten_toolchain() to select Emscripten
- Building a C program that compiles to .js + .wasm
- The output can be run with: node build/hello.js

Prerequisites:
  Install Emscripten: https://emscripten.org/docs/getting_started/
  Set EMSDK or activate the emsdk environment.
"""

import os

from pcons import Generator, Project, find_emscripten_toolchain

project = Project(
    "hello_emscripten", build_dir=os.environ.get("PCONS_BUILD_DIR", "build")
)
env = project.Environment(toolchain=find_emscripten_toolchain())

project.Program("hello", env, sources=["src/hello.c"])

Generator().generate(project)
