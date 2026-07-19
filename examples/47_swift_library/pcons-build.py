#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A Swift library imported by a Swift program.

Demonstrates:
- A Swift static library target (its own module, compiled whole-module)
- Cross-module import: the library's .swiftmodule search path propagates
  to dependents as an ordinary usage requirement
- A Test() target running the built program
"""

from pcons import Project

project = Project("swift_library")
env = project.Environment(toolchain="swift")

geometry = project.StaticLibrary(
    "Geometry", env, sources=["geometry/geometry.swift", "geometry/util.swift"]
)

app = project.Program("shapes", env, sources=["src/main.swift"])
app.link_private(geometry)

project.Test("shapes.selftest", app, args=["--test"], labels=["unit"])
