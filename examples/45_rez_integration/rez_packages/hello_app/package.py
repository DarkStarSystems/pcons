# SPDX-License-Identifier: MIT
"""Test rez package: a hello-world app built via the pcons build_system.

- ``build_system = "pcons"`` invokes :class:`pcons.integrations.rez.
  build_system.PconsBuildSystem`, which runs ``pcons generate`` then
  ``ninja`` inside the rez-resolved build env.
- The ``pcons-build.py`` calls :func:`rez_environment` to pick up
  ``hello_lib``'s include/lib settings from the rez resolve.
"""

name = "hello_app"
version = "0.1.0"

authors = ["pcons"]
description = "Test rez package using pcons as build_system"

build_system = "pcons"

requires = ["hello_lib"]


def commands():
    env.PATH.append("{root}/bin")  # noqa: F821
