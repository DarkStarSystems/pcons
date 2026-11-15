# SPDX-License-Identifier: MIT
"""Build script for a C++ program linked with static language runtimes.

This example demonstrates:
- The same program built twice, with the shared runtimes (the default)
  and with static ones (env.set_runtime_linkage("static"))
- The static program needs no C++ runtime installed where it runs:
  libstdc++ on Linux, the VCRUNTIME and MSVCP DLLs with MSVC
"""

from pcons import Project

project = Project("static_runtime")
env = project.Environment(toolchain="c++")
project.Program("app_shared", env, sources=["main.cpp"])

static = env.clone()
static.set_runtime_linkage("static")
project.Program("app_static", static, sources=["main.cpp"])
