# SPDX-License-Identifier: MIT
"""Build script for a program checked by AddressSanitizer.

This example demonstrates:
- The sanitize preset, with each compiler's AddressSanitizer (and its
  UndefinedBehaviorSanitizer where it has one)
- relwithdebinfo: optimized, with debug info for the sanitizer's reports,
  and the variant every compiler can sanitize (clang-cl's AddressSanitizer
  has no debug C runtime)
"""

from pcons import Project

project = Project("sanitize")
env = project.Environment(toolchain="c++")
env.set_variant("relwithdebinfo")
env.apply_preset("sanitize")
project.Program("app", env, sources=["main.cpp"])
