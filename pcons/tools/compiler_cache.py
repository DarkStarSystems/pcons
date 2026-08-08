# SPDX-License-Identifier: MIT
"""Compiler caches (ccache, sccache) as command launchers.

Knowing which caches exist, and that ccache cannot drive MSVC, is tool
knowledge, so it lives here rather than in the core. All this does is choose a
program and set it as the launcher on the compile tools; see
:mod:`pcons.core.launcher` for what a launcher is.
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment

logger = logging.getLogger("pcons")

#: Caches to try when none is named, best first.
KNOWN_CACHES = ("sccache", "ccache")

#: Tools worth caching. Linking and archiving gain nothing from a cache.
CACHED_TOOLS = ("cc", "cxx")


def apply_compiler_cache(env: Environment, tool: str | None = None) -> None:
    """Run the compile tools of *env* behind a compiler cache.

    Missing tools are skipped, and a cache that isn't installed is a warning
    rather than an error: a build should still work on a machine without one.
    """
    if tool is None:
        tool = next((name for name in KNOWN_CACHES if shutil.which(name)), None)
        if tool is None:
            logger.warning(
                "No compiler cache found (tried %s)", ", ".join(KNOWN_CACHES)
            )
            return
    elif tool not in KNOWN_CACHES:
        logger.warning("Unknown compiler cache tool '%s'", tool)
        return

    if not shutil.which(tool):
        logger.warning("Compiler cache '%s' not found in PATH", tool)
        return

    if tool == "ccache" and _drives_msvc(env):
        logger.warning("ccache does not support MSVC cl.exe; use sccache instead")
        return

    for tool_name in CACHED_TOOLS:
        if not env.has_tool(tool_name):
            continue
        tool_config = getattr(env, tool_name)
        if tool not in tool_config.launcher:
            tool_config.launcher = [*tool_config.launcher, tool]


def _drives_msvc(env: Environment) -> bool:
    """Whether either compile tool is cl.exe."""
    for tool_name in CACHED_TOOLS:
        if not env.has_tool(tool_name):
            continue
        cmd = getattr(env, tool_name).get("cmd", "")
        if isinstance(cmd, str) and (cmd.endswith("cl.exe") or cmd.endswith("cl")):
            return True
    return False
