# SPDX-License-Identifier: MIT
"""Built-in builders for pcons.

This package contains the built-in builders that are registered with the
BuilderRegistry. All builders (built-in and user-defined) register through
the same system, ensuring user-defined builders are on equal footing with
built-ins.

Built-in builders:
- Install, InstallAs, InstallDir: File installation builders (pcons.tools.install)
- Tarfile, Zipfile: Archive builders (pcons.tools.archive)
- Program, StaticLibrary, SharedLibrary, ObjectLibrary: Compile/link builders
- HeaderOnlyLibrary: Interface library builder
- Command: Custom command builder
"""

from __future__ import annotations


def register_builtin_builders() -> None:
    """Register all built-in builders with the BuilderRegistry.

    This is called during pcons initialization to ensure all built-in
    builders are available on Project instances.
    """
    # Import builder modules to trigger their registration
    # Each module uses the @builder decorator to register its builders
    from pcons.builders import compile  # noqa: F401

    # Install and Archive builders are now in pcons.tools (merged with tools)
    from pcons.tools import archive, install  # noqa: F401
