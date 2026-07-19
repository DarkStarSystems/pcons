#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-compile Swift for iOS with the ios() cross preset.

Two lines select the platform: the Swift toolchain by name, and the
ios() preset (which resolves the iPhoneOS SDK via xcrun and sets the
-target triple for both compile and link).
"""

from pcons import Project
from pcons.toolchains.presets import ios

project = Project("swift_ios")
env = project.Environment(toolchain="swift")
env.apply_cross_preset(ios(arch="arm64", min_version="15.0"))

project.Program("hello_ios", env, sources=["src/main.swift"])
