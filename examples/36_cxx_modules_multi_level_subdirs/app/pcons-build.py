#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for app - can be built standalone or as part of parent project.

This demonstrates a subdir that depends on another subdir (libfoo).
Works both standalone and as part of the parent build.
"""

from pcons import get_targets, program

program("app").add_sources(["main.cpp"]).link(*get_targets("a", "aa", "b", "bb"))
