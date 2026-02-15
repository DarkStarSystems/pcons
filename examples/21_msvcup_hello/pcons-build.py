# SPDX-License-Identifier: MIT
"""Example: Building with msvcup-managed MSVC toolchain.

This example downloads and installs the MSVC compiler via msvcup,
then builds a simple C program. No Visual Studio installation required.
Works on both x64 and ARM64 Windows machines -- target_cpu is auto-detected.
"""

import os
import sys

from pcons import Generator, Project, find_c_toolchain

if sys.platform == "win32":
    from pcons.contrib.windows.msvcup import ensure_msvc

    # target_cpu auto-detected: x64 on x86_64, arm64 on ARM64
    ensure_msvc("14.44.17.14", "10.0.22621.7")

project = Project("hello", build_dir=os.environ.get("PCONS_BUILD_DIR", "build"))
env = project.Environment(toolchain=find_c_toolchain())
project.Program("hello", env, sources=["src/hello.c"])
Generator().generate(project)
