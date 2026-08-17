#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""An ordinary build script, driven two ways.

`pcons` runs it as usual. `driver.py` in this directory runs it too — as
one step of its own program — to show pcons used as a library. See
`docs/library.md`.
"""

from pcons import Project

project = Project("embedded_hello")
env = project.Environment(toolchain="c")
project.Program("hello", env, sources=["src/hello.c"])
