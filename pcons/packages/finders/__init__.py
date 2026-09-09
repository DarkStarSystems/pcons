# SPDX-License-Identifier: MIT
"""Package finders for pcons.

This module provides various ways to find external packages:
- PkgConfigFinder: Uses pkg-config
- SystemFinder: Searches standard system paths
- ConanFinder: Uses Conan 2.x package manager
- FinderChain: Tries multiple finders in order

Integration-specific finders live under :mod:`pcons.integrations`. For
example, :class:`pcons.integrations.rez.RezFinder` reads a rez resolve.
"""

import os
from pathlib import Path

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
    "host_finders",
    "sysroot_finders",
]

_SYSROOT_PKGCONFIG_DIRS = ("usr/lib/pkgconfig", "usr/share/pkgconfig")
_SYSROOT_INCLUDE_DIRS = ("usr/include", "include")
_SYSROOT_LIB_DIRS = ("usr/lib", "lib")


def host_finders() -> list[BaseFinder]:
    """The finders for a build that runs on the machine it is built on.

    pkg-config first, then a search of the machine's own header and library
    directories.
    """
    return [PkgConfigFinder(), SystemFinder()]


def sysroot_finders(sysroot: Path) -> list[BaseFinder]:
    """The finders for a build targeting the tree rooted at *sysroot*.

    ``PKG_CONFIG_SYSROOT_DIR`` alone is not enough and is worse than
    nothing: it rewrites the paths a ``.pc`` file reports, but pkg-config
    still reads the *host's* ``.pc`` files, so a host package is reported as
    living in the sysroot. ``PKG_CONFIG_LIBDIR`` is what moves the search
    itself, so both are set.

    The directory names below the sysroot are the usual layout, either the
    filesystem hierarchy a Linux sysroot copies or the flat one a bare-metal
    toolchain ships. A sysroot laid out some other way finds nothing here,
    which is the safe direction: add a finder for it with
    ``project.add_package_finder(finder, env=...)``.
    """
    return [
        PkgConfigFinder(
            env_overrides={
                "PKG_CONFIG_SYSROOT_DIR": str(sysroot),
                "PKG_CONFIG_LIBDIR": os.pathsep.join(
                    str(sysroot / d) for d in _SYSROOT_PKGCONFIG_DIRS
                ),
            }
        ),
        SystemFinder(
            include_paths=[sysroot / d for d in _SYSROOT_INCLUDE_DIRS],
            library_paths=[sysroot / d for d in _SYSROOT_LIB_DIRS],
        ),
    ]
