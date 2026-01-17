# SPDX-License-Identifier: MIT
"""macOS-specific utilities for pcons.

These utilities help with common macOS build tasks like managing
dynamic library paths for bundles.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_dylib_install_name(path: Path | str) -> str:
    """Get a dylib's install name.

    The install name is the path recorded in binaries that link to this
    library. It's often different from the actual filename (e.g., a
    symlink name like "libfoo.1.dylib" vs the real file "libfoo.1.2.3.dylib").

    Args:
        path: Path to the dylib file.

    Returns:
        The basename of the install name (e.g., "libopencv_core.412.dylib").

    Raises:
        subprocess.CalledProcessError: If otool fails.
        ValueError: If the dylib has no install name.

    Example:
        >>> install_name = get_dylib_install_name("/opt/homebrew/lib/libfoo.1.2.3.dylib")
        >>> # Returns "libfoo.1.dylib" (the install name, not the filename)
        >>> plugin.post_build(f"install_name_tool -change /opt/homebrew/lib/{install_name} @rpath/{install_name} $out")
        >>> project.InstallAs(bundle_dir / install_name, dylib_path)
    """
    path = Path(path)
    output = subprocess.check_output(
        ["otool", "-D", str(path)],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()

    # Output format is:
    # /path/to/lib.dylib:
    # /install/path/lib.dylib
    lines = output.split('\n')
    if len(lines) < 2:
        raise ValueError(f"No install name found for {path}")

    install_path = lines[-1].strip()
    return Path(install_path).name


def fix_dylib_references(
    target,
    dylibs: list[Path | str],
    lib_dir: Path | str,
    *,
    rpath_prefix: str = "@rpath",
) -> list[str]:
    """Add post_build commands to fix dylib references in a target.

    This is a convenience function that generates install_name_tool commands
    to change absolute dylib paths to rpath-relative paths.

    Args:
        target: The Target to add post_build commands to.
        dylibs: List of dylib paths to fix references for.
        lib_dir: The directory where dylibs are installed (the absolute path
                 that will be replaced).
        rpath_prefix: The rpath prefix to use (default: "@rpath").

    Returns:
        List of install names (basenames) for the dylibs, useful for
        InstallAs operations.

    Example:
        >>> install_names = fix_dylib_references(
        ...     ofx_plugin,
        ...     opencv_dylibs,
        ...     "/opt/homebrew/opt/opencv/lib",
        ... )
        >>> for dylib, name in zip(opencv_dylibs, install_names):
        ...     project.InstallAs(bundle_dir / name, dylib)
    """
    lib_dir = Path(lib_dir)
    install_names = []

    for dylib in dylibs:
        dylib = Path(dylib)
        install_name = get_dylib_install_name(dylib)
        install_names.append(install_name)

        target.post_build(
            f"install_name_tool -change {lib_dir}/{install_name} "
            f"{rpath_prefix}/{install_name} $out"
        )

    return install_names
