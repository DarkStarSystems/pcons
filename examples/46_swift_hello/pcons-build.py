#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A Swift program: all .swift files in a target compile as one module.

Demonstrates:
- Selecting the Swift toolchain by name: toolchain="swift"
- Whole-module compilation (files see each other without imports)
"""

from pcons import Project

project = Project("swift_hello")
env = project.Environment(toolchain="swift")
project.Program("hello", env, sources=["src/main.swift", "src/greeter.swift"])
