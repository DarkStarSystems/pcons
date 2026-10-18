# SPDX-License-Identifier: MIT
"""Build script for a C++ program calling a CUDA kernel.

This example demonstrates:
- nvcc compiling the .cu source beside a C++ compiler
- The C++ driver linking the CUDA runtime, which nvcc would add itself
- env.cuda.runtime choosing it: "static" (the default), "shared" or "none"
"""

from pcons import Project

project = Project("cuda_runtime")
env = project.Environment(toolchain="c++")
env.add_toolchain("cuda")

sources = ["src/main.cpp", "src/kernel.cu"]

# The static CUDA runtime is part of the program.
project.Program("app_static", env, sources=sources)

# The shared one is loaded when it runs: libcudart.so.12 or cudart64_12.dll.
shared = env.clone()
shared.cuda.runtime = "shared"
project.Program("app_shared", shared, sources=sources)
