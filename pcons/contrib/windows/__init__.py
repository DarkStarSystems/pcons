# SPDX-License-Identifier: MIT
"""Windows-specific helpers for pcons.

This package provides helpers for Windows development:
- manifest: Windows SxS manifest generation (app and assembly manifests)
- msvcup: MSVC toolchain installation via msvcup (no Visual Studio required)

Usage:
    from pcons.contrib.windows import manifest

    # Generate app manifest with DPI awareness
    app_manifest = manifest.create_app_manifest(
        project, env,
        output="app.manifest",
        dpi_aware="PerMonitorV2",
        visual_styles=True,
    )

    # Use it in the build (manifest automatically embedded via /MANIFESTINPUT)
    app = project.Program("myapp", env)
    app.add_sources(["src/main.c", app_manifest])

    # Install MSVC without Visual Studio (call before find_c_toolchain)
    from pcons.contrib.windows.msvcup import ensure_msvc
    ensure_msvc("14.44.17.14", "10.0.22621.7")

Available modules:
    - manifest: Windows SxS manifest generation
    - msvcup: MSVC toolchain installation via msvcup
"""

from __future__ import annotations


def list_modules() -> list[str]:
    """List available Windows modules.

    Returns:
        List of module names in the windows package.
    """
    return ["manifest", "msvcup"]
