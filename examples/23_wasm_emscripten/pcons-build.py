#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple Emscripten WebAssembly program.

This example demonstrates:
- Selecting the Emscripten toolchain by name: toolchain="emscripten"
- Building a C program that compiles to .js + .wasm
- The output can be run with: node build/hello.js

Prerequisites:
  Install Emscripten: https://emscripten.org/docs/getting_started/
  Set EMSDK or activate the emsdk environment.
"""

from pcons import Project

project = Project("hello_emscripten")
env = project.Environment(toolchain="emscripten")

project.Program("hello", env, sources=["src/hello.c"])
