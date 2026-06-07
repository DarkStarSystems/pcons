#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Nested projects metadata example.

Three levels of nested Project() instances, each created via
add_subdirectory:

    nested_root              (this file)
    +-- nested_child         (child/pcons-build.py)
        +-- nested_grandchild (child/grandchild/pcons-build.py)

Every level creates its OWN Project and defines one Program. This exercises
the IDE metadata generator (pcons_metadata.json): each project must get its
own entry, and each entry must list only its own targets - no target may
appear under an ancestor project.

Validated by test_metadata.py via the example test runner.
"""

from pathlib import Path

from pcons import (
    Generator,
    MetadataGenerator,
    Project,
    add_subdirectory,
    find_c_toolchain,
)

project = Project("nested_root", root_dir=Path(__file__).parent)
env = project.Environment(toolchain=find_c_toolchain())

root_app = project.Program("root_app", env, sources=["src/root.c"])

add_subdirectory("child")

# Run the structural generator selected by PCONS_GENERATOR, and always also
# emit the IDE metadata so the example can be validated regardless of which
# backend is used.
Generator().generate(project)
MetadataGenerator().generate(project)
