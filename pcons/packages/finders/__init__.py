# SPDX-License-Identifier: MIT
"""Package finders for pcons.

This module provides various ways to find external packages:
- PkgConfigFinder: Uses pkg-config
- SystemFinder: Searches standard system paths
- ConanFinder: Uses Conan 2.x package manager
- FinderChain: Tries multiple finders in order
"""

from pcons.packages.finders.base import BaseFinder, FinderChain
from pcons.packages.finders.conan import ConanFinder
from pcons.packages.finders.pkgconfig import PkgConfigFinder
from pcons.packages.finders.system import SystemFinder

__all__ = [
    "BaseFinder",
    "ConanFinder",
    "FinderChain",
    "PkgConfigFinder",
    "SystemFinder",
]
