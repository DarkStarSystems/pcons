#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Two independent projects in one build script.

A firmware-style split: the same source tree built two ways, each as its
own top-level project with its own build directory and build.ninja. One
`pcons` run generates and builds both, in script order.

The first project keeps the default build directory (so `-B` works); each
later sibling names its own. Both embed the same `common/` subdirectory,
compiled per project with that project's flags.

Run `pcons`, then `./build/app` and `./build-host/app`.
"""

from pcons import Project, find_c_toolchain

toolchain = find_c_toolchain()

# The device flavor: default build directory.
device = Project("device")
denv = device.Environment(toolchain=toolchain)
denv.cc.defines.append("DEVICE_BUILD")
dcommon = device.add_subdirectory("common")
dapp = device.Program("app", denv, sources=["src/main.c"])
dapp.link(dcommon.common)
device.Default(dapp)

# The host flavor: an independent sibling, with its own build directory,
# derived from the first project's so `pcons -B out` yields out and out-host.
host = Project("host", build_dir=f"{device.build_dir}-host")
henv = host.Environment(toolchain=toolchain)
hcommon = host.add_subdirectory("common")
happ = host.Program("app", henv, sources=["src/main.c"])
happ.link(hcommon.common)
host.Default(happ)
