# SPDX-License-Identifier: MIT
"""Pcons contributed modules.

These are generic helper modules that ship with pcons.
For domain-specific modules (OFX, AE, etc.), create your own
in ~/.pcons/modules/ or ./pcons_modules/.

Usage:
    from pcons.contrib import bundle
    bundle.create_macos_bundle(project, env, plugin, ...)

    from pcons.contrib.installers import macos
    macos.create_pkg(project, env, name="MyApp", ...)

Available modules:
    - bundle: Generic bundle creation helpers for macOS and flat bundles
    - installers: Platform-native installer creation (macOS .pkg/.dmg, etc.)
    - platform: Platform detection utilities
"""

from __future__ import annotations

import pkgutil


def list_modules() -> list[str]:
    """List available contrib modules.

    Returns:
        List of module names in the contrib package.
    """
    return [name for _, name, _ in pkgutil.iter_modules(__path__)]
