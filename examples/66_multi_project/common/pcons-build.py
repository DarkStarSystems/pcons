# SPDX-License-Identifier: MIT
"""A library embedded by both top-level projects, compiled per project."""

from pcons import context

project = context.current_project
env = project.default_environment

common = project.StaticLibrary("common", env, sources=["common.c"])
common.public.include_dirs.append(".")
