#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for the hello_app rez package.

Invoked by :class:`pcons.integrations.rez.build_system.PconsBuildSystem`
from inside ``rez-build``. Reads ``PCONS_BUILD_DIR``, ``PCONS_SOURCE_DIR``,
and (when installing) ``PCONS_INSTALL_DIR`` — all set by the plugin.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain
from pcons.integrations.rez import is_in_rez_resolve, rez_environment

source_dir = Path(os.environ.get("PCONS_SOURCE_DIR", Path(__file__).parent))
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", source_dir / "build"))
install_dir = os.environ.get("PCONS_INSTALL_DIR")

project = Project("hello_app", root_dir=source_dir, build_dir=build_dir)

toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)
env.cxx.flags.append("-std=c++17")
env.link.cmd = env.cxx.cmd  # link with C++ driver to pick up libstdc++/libc++

if is_in_rez_resolve():
    rez_environment(env)

app = project.Program("hello_app", env, sources=[source_dir / "src" / "main.cpp"])
project.Default(app)

if install_dir:
    install_target = project.Install(f"{install_dir}/bin", [app])
    project.Alias("install", install_target)

Generator().generate(project)
