# SPDX-License-Identifier: MIT
"""Configure phase: tool detection, feature checks, platform detection."""

from pcons.configure.checks import CheckResult, ToolChecks
from pcons.configure.config import Configure, ProgramInfo, load_config
from pcons.configure.config_file import configure_file
from pcons.configure.platform import Platform, detect_platform, get_platform

__all__ = [
    "CheckResult",
    "Configure",
    "Platform",
    "ProgramInfo",
    "ToolChecks",
    "configure_file",
    "detect_platform",
    "get_platform",
    "load_config",
]
