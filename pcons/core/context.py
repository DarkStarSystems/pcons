# SPDX-License-Identifier: MIT
"""Core context and global state management for pcons build scripts."""

from __future__ import annotations

import logging

from pcons.core.project import Project

logger = logging.getLogger(__name__)


class Context:
    """Global context for pcons build scripts."""

    @property
    def current_project(self) -> Project:
        """Current active Project instance."""
        return Project.current()

    def get_target(self, name: str):
        """Get a target by name from the current project."""
        return self.current_project.get_target(name)

    def get_targets(self, *names: str):
        """Get all targets from the current project."""
        return self.current_project.get_targets(*names)


context = Context()
"""Internal context object for storing global state and providing convenient accessors.

Example usage in pcons-build.py included with `add_subdirectory`:
    from pcons import context
    project = context.current_project
    env = project.default_environment
    libfoo = project.StaticLibrary("foo", env).add_sources(["foo.c"])
"""
