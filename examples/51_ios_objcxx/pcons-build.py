#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-compile C++ and Objective-C++ for iOS with the ios() cross preset.

The same ios() preset used for Swift (example 49) works with the LLVM
toolchain: it sets the -target triple on compile and link, and the
iPhoneOS SDK is resolved via xcrun automatically. Objective-C++ (.mm)
sources compile with the C++ tool and can use Apple frameworks.
"""

from pcons import Project
from pcons.toolchains.presets import ios

project = Project("ios_objcxx")
env = project.Environment(toolchain="llvm")
env.apply_cross_preset(ios(arch="arm64", min_version="15.0"))
env.link.frameworks.append("Foundation")

project.Program("hello_ios", env, sources=["src/main.mm", "src/greeting.cpp"])
