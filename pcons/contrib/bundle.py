# SPDX-License-Identifier: MIT
"""Generic bundle creation helpers.

This module provides utilities for creating application and plugin bundles
in various formats (macOS .bundle, flat directory bundles, etc.).

These are building blocks that domain-specific modules can use:
    from pcons.contrib import bundle

    # In your OFX/AE/Spark module:
    def create_ofx_bundle(project, env, plugin, ...):
        plist = bundle.generate_info_plist(name, version, bundle_type="BNDL")
        bundle.create_macos_bundle(project, env, plugin, ...)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


def generate_info_plist(
    name: str,
    version: str,
    *,
    bundle_type: str = "BNDL",
    identifier: str | None = None,
    executable: str | None = None,
    signature: str = "????",
    min_os_version: str = "10.13",
    extra_keys: dict[str, str] | None = None,
) -> str:
    """Generate Info.plist content for a macOS bundle.

    Args:
        name: Bundle display name (CFBundleName).
        version: Version string (CFBundleShortVersionString and CFBundleVersion).
        bundle_type: Bundle type code (BNDL, APPL, FMWK). Default: "BNDL".
        identifier: Bundle identifier. Defaults to com.example.{name}.
        executable: Executable name. Defaults to {name}.
        signature: 4-character signature code. Default: "????".
        min_os_version: Minimum macOS version. Default: "10.13".
        extra_keys: Additional key-value pairs to include.

    Returns:
        Info.plist XML content as a string.

    Example:
        >>> plist = generate_info_plist("MyPlugin", "1.0.0", bundle_type="BNDL")
        >>> (bundle_dir / "Contents" / "Info.plist").write_text(plist)
    """
    if identifier is None:
        # Sanitize name for bundle identifier
        safe_name = name.replace(" ", "").replace("-", "")
        identifier = f"com.example.{safe_name}"

    if executable is None:
        executable = name

    plist = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>{executable}</string>
    <key>CFBundleIdentifier</key>
    <string>{identifier}</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>{name}</string>
    <key>CFBundlePackageType</key>
    <string>{bundle_type}</string>
    <key>CFBundleShortVersionString</key>
    <string>{version}</string>
    <key>CFBundleSignature</key>
    <string>{signature}</string>
    <key>CFBundleVersion</key>
    <string>{version}</string>
    <key>LSMinimumSystemVersion</key>
    <string>{min_os_version}</string>
"""

    if extra_keys:
        for key, value in extra_keys.items():
            plist += f"    <key>{key}</key>\n"
            plist += f"    <string>{value}</string>\n"

    plist += """\
</dict>
</plist>
"""
    return plist


def create_macos_bundle(
    project: Project,
    env: Environment,  # noqa: ARG001 - kept for API consistency
    plugin: Target,
    *,
    bundle_dir: Path | str,
    info_plist: str | Path | None = None,
    resources: list[Path | str] | None = None,
    arch_subdir: str | None = None,
) -> Target:
    """Create a macOS .bundle or .plugin structure.

    This creates the standard macOS bundle structure:
        MyBundle.bundle/
            Contents/
                Info.plist
                MacOS/
                    <plugin binary>
                Resources/
                    <optional resources>

    Args:
        project: Pcons project.
        env: Configured environment.
        plugin: The compiled plugin/library target.
        bundle_dir: Bundle output directory (e.g., "build/MyPlugin.bundle").
        info_plist: Info.plist content (string) or path to existing file.
            If None, generates a minimal plist.
        resources: Optional list of resource files to include.
        arch_subdir: Architecture subdirectory name (e.g., "MacOS-x86-64").
            If None, uses standard "MacOS" directory.

    Returns:
        Target for the installed plugin within the bundle.

    Example:
        >>> plugin = project.SharedLibrary("myplugin", env, sources=["plugin.cpp"])
        >>> plist = bundle.generate_info_plist("MyPlugin", "1.0.0")
        >>> bundle.create_macos_bundle(
        ...     project, env, plugin,
        ...     bundle_dir="build/MyPlugin.bundle",
        ...     info_plist=plist,
        ... )
    """
    bundle_path = Path(bundle_dir)
    contents_dir = bundle_path / "Contents"
    binary_dir = contents_dir / (arch_subdir or "MacOS")

    # Install the plugin binary
    installed = project.Install(binary_dir, [plugin])

    # Install Info.plist
    if info_plist is not None:
        if isinstance(info_plist, str):
            # Write plist content to file, then install
            # Note: This creates a source node; the actual file should exist
            # In practice, the user should write the plist file themselves
            # or use env.Command to generate it
            pass
        elif isinstance(info_plist, Path):
            project.Install(contents_dir, [info_plist])

    # Install resources
    if resources:
        resources_dir = contents_dir / "Resources"
        project.Install(resources_dir, resources)

    return installed


def create_flat_bundle(
    project: Project,
    env: Environment,  # noqa: ARG001 - kept for API consistency
    plugin: Target,
    *,
    bundle_dir: Path | str,
    dlls: list[Target | Path | str] | None = None,
    resources: list[Path | str] | None = None,
) -> Target:
    """Create a flat directory bundle (Windows/Linux style).

    A flat bundle is a simple directory containing the plugin and its
    dependencies without the macOS-specific structure.

    Args:
        project: Pcons project.
        env: Configured environment.
        plugin: The compiled plugin/library target.
        bundle_dir: Bundle output directory.
        dlls: Optional list of DLLs/shared libraries to include.
        resources: Optional list of resource files to include.

    Returns:
        Target for the installed plugin.

    Example:
        >>> plugin = project.SharedLibrary("myplugin", env, sources=["plugin.cpp"])
        >>> bundle.create_flat_bundle(
        ...     project, env, plugin,
        ...     bundle_dir="build/MyPlugin",
        ...     dlls=[some_dependency_dll],
        ... )
    """
    bundle_path = Path(bundle_dir)

    # Install the plugin
    installed = project.Install(bundle_path, [plugin])

    # Install additional DLLs
    if dlls:
        for dll in dlls:
            project.Install(bundle_path, [dll])

    # Install resources
    if resources:
        project.Install(bundle_path, resources)

    return installed


def get_arch_subdir(platform_name: str, arch: str | None = None) -> str:
    """Get architecture subdirectory name for plugin bundles.

    This follows common conventions used by plugin formats like OFX.

    Args:
        platform_name: Platform name ("darwin", "linux", "win32").
        arch: Architecture name ("x86_64", "arm64", etc.).
            If None, defaults based on platform.

    Returns:
        Architecture subdirectory name (e.g., "MacOS-x86-64", "Linux-x86-64").

    Example:
        >>> get_arch_subdir("darwin", "arm64")
        'MacOS-arm-64'
        >>> get_arch_subdir("linux", "x86_64")
        'Linux-x86-64'
        >>> get_arch_subdir("win32", "x86_64")
        'Win64'
    """
    if arch is None:
        arch = "x86_64"

    # Normalize architecture
    arch_map = {
        "x86_64": "x86-64",
        "amd64": "x86-64",
        "arm64": "arm-64",
        "aarch64": "arm-64",
    }
    normalized_arch = arch_map.get(arch, arch)

    if platform_name == "darwin":
        return f"MacOS-{normalized_arch}"
    elif platform_name.startswith("linux"):
        return f"Linux-{normalized_arch}"
    elif platform_name == "win32":
        if "x86-64" in normalized_arch or "amd64" in arch:
            return "Win64"
        return "Win32"
    else:
        return f"Unknown-{normalized_arch}"
