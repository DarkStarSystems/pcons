#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple WASI WebAssembly program.

This example demonstrates:
- Selecting the wasi-sdk toolchain by name: toolchain="wasi"
- Building a C program that compiles to a .wasm file
- The output can be run with any WASI runtime: wasmtime, wasmer, etc.

Prerequisites:
  Install wasi-sdk: https://github.com/WebAssembly/wasi-sdk
  Set WASI_SDK_PATH or install to /opt/wasi-sdk.
"""

from pcons import Project

project = Project("hello_wasi")
env = project.Environment(toolchain="wasi")

project.Program("hello", env, sources=["src/hello.c"])
