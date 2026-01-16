# SPDX-License-Identifier: MIT
"""Package management for pcons.

This module provides:
- PackageDescription: Description of an external package
- ImportedTarget: Target wrapper for external dependencies
- Package finders: PkgConfigFinder, SystemFinder, etc.
"""

from pcons.packages.description import ComponentDescription, PackageDescription
from pcons.packages.imported import ImportedTarget

__all__ = [
    "ComponentDescription",
    "ImportedTarget",
    "PackageDescription",
]
