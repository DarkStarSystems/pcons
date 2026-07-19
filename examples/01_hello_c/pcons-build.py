#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The simplest pcons build: compile one C file into a program.

Run `pcons` to build, then `./build/hello` to run it.
"""

from pcons import Project

project = Project("hello_c")
env = project.Environment(toolchain="c")
project.Program("hello", env, sources=["src/hello.c"])
