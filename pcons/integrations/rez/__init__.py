# SPDX-License-Identifier: MIT
"""Rez (https://rez.readthedocs.io) integration for pcons.

Two-sided integration:

1. **Read the rez resolve from inside pcons.** When a ``pcons-build.py``
   runs inside a ``rez-env`` shell, :func:`rez_environment` injects every
   resolved rez package's include/lib/define settings into the pcons
   :class:`Environment`. :class:`RezFinder` plugs into pcons's package
   finder chain so ``project.find_package(name)`` resolves rez packages.

2. **Plug pcons into rez-build.** :mod:`pcons.integrations.rez.build_system`
   exposes ``PconsBuildSystem``, a rez ``build_system`` plugin registered
   via the ``rez.plugins`` entry point in pcons's ``pyproject.toml``.

Both sides are independent; you can use either alone.
"""

from pcons.integrations.rez.env import (
    ResolvedPackage,
    is_in_rez_resolve,
    package_description,
    resolved_packages,
    rez_environment,
)
from pcons.integrations.rez.finder import RezFinder

__all__ = [
    "ResolvedPackage",
    "RezFinder",
    "is_in_rez_resolve",
    "package_description",
    "resolved_packages",
    "rez_environment",
]
