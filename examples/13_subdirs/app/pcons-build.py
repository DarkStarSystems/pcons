#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for app - can be built standalone or as part of parent project.

This demonstrates a subdir that depends on another subdir (libfoo).
Works both standalone and as part of the parent build.
"""

from pcons import Project

project = Project.current()
assert project is not None
env = project.environments[0]

libfoo = project.get_target("foo")
assert libfoo is not None, (
    "libfoo target not found - ensure libfoo's pcons-build.py is run first"
)

app = project.Program("app", env)
app.add_sources(["src/main.c"])
app.link(libfoo)
