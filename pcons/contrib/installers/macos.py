# SPDX-License-Identifier: MIT
"""macOS installer creation helpers.

This module provides functions for creating macOS installers:
- create_pkg(): Create .pkg installers using pkgbuild/productbuild
- create_component_pkg(): Create simple component packages with pkgbuild
- create_dmg(): Create .dmg disk images using hdiutil

These functions integrate with the pcons build system, generating proper
ninja rules with dependencies for incremental builds.

Requirements:
    - pkgbuild and productbuild (included with Xcode Command Line Tools)
    - hdiutil (included with macOS)

Example:
    from pcons.contrib.installers import macos

    # Create a .pkg installer
    pkg = macos.create_pkg(
        project, env,
        name="MyApp",
        version="1.0.0",
        identifier="com.example.myapp",
        sources=[app],
        install_location="/usr/local/bin",
    )

    # Create a .dmg disk image
    dmg = macos.create_dmg(
        project, env,
        name="MyApp",
        sources=[app],
    )
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.contrib.installers._helpers import check_tool

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


# Reserved staging directory prefixes for installer generation.
# These are used in the build directory and should not conflict with user outputs.
_RESERVED_STAGING_PREFIXES = frozenset(
    {".pkg_staging", ".dmg_staging", ".msix_staging"}
)


def _validate_staging_path(project: Project, staging_prefix: str) -> None:
    """Validate that the staging path doesn't conflict with user build outputs.

    Checks that no existing targets or nodes in the project have paths that would
    conflict with the staging directory. This prevents accidental overwrites and
    ensures installer staging is isolated from user outputs.

    Args:
        project: The project to check for conflicts.
        staging_prefix: The staging directory prefix (e.g., ".pkg_staging").

    Raises:
        ValueError: If a conflict is detected with existing build outputs.
    """
    staging_path = project.build_dir / staging_prefix

    # Check for conflicts with existing targets' output nodes
    for target in project.targets:
        for node in target.output_nodes:
            node_path = node.path
            try:
                # Check if node path would be under or conflict with staging
                node_path.relative_to(staging_path)
                raise ValueError(
                    f"Installer staging path '{staging_path}' conflicts with "
                    f"target '{target.name}' output: {node_path}. "
                    f"Rename the target's output or use a different build directory."
                )
            except ValueError:
                # Not under staging path - this is expected
                pass

    # Check for conflicts with existing nodes in project
    for node_path in project._nodes:
        try:
            node_path.relative_to(staging_path)
            raise ValueError(
                f"Installer staging path '{staging_path}' conflicts with "
                f"existing build node: {node_path}. "
                f"This may indicate a naming conflict in your build configuration."
            )
        except ValueError:
            # Not under staging path - this is expected
            pass


def create_component_pkg(
    project: Project,
    env: Environment,
    *,
    identifier: str,
    version: str,
    sources: list[Target | FileNode | Path | str],
    install_location: str = "/Applications",
    output: str | Path | None = None,
    scripts_dir: Path | None = None,
    component_plist: Path | None = None,
    ownership: str = "recommended",
    sign_identity: str | None = None,
) -> Target:
    """Create a macOS component package using pkgbuild.

    Component packages are simple packages containing a single payload.
    They can be used standalone or combined into a product archive
    using productbuild.

    Args:
        project: Pcons project.
        env: Configured environment.
        identifier: Bundle identifier (e.g., "com.example.myapp").
        version: Package version string.
        sources: Files or directories to include (Targets or paths).
            Directory sources are automatically detected and copied with
            depfile tracking after resolve().
        install_location: Where files install (e.g., "/Applications").
        output: Output .pkg path. Defaults to build/<identifier>-<version>.pkg.
        scripts_dir: Directory containing preinstall/postinstall scripts.
        component_plist: Path to component plist file for bundle settings.
        ownership: File ownership ("recommended", "preserve", "preserve-other").
        sign_identity: Code signing identity (e.g., "Developer ID Installer: Name").

    Returns:
        Target representing the .pkg file.

    Raises:
        ToolNotFoundError: If pkgbuild is not found.
        ValueError: If staging path conflicts with existing build outputs.
    """
    check_tool("pkgbuild", "Install Xcode Command Line Tools: xcode-select --install")

    if output is None:
        output = Path(f"{identifier}-{version}.pkg")
    else:
        output = Path(output)

    # Validate staging path doesn't conflict with user outputs
    _validate_staging_path(project, ".pkg_staging")

    # Stage files to a temporary directory (rel paths are relative to build_dir)
    staging_rel = Path(".pkg_staging") / identifier / "payload"

    # Stage source files (Install auto-detects directory sources after resolve)
    stage_target = project.Install(staging_rel, sources)

    # Build pkgbuild command (paths relative to build_dir where ninja/make run)
    pkgbuild_args = [
        "pkgbuild",
        "--root",
        str(staging_rel),
        "--identifier",
        identifier,
        "--version",
        version,
        "--install-location",
        install_location,
        "--ownership",
        ownership,
    ]

    if scripts_dir is not None:
        pkgbuild_args.extend(["--scripts", str(scripts_dir)])

    if component_plist is not None:
        pkgbuild_args.extend(["--component-plist", str(component_plist)])

    if sign_identity is not None:
        pkgbuild_args.extend(["--sign", sign_identity])

    pkgbuild_args.append(str(output))

    return env.Command(
        target=output,
        source=[stage_target],
        command=pkgbuild_args,
        name=f"pkg_{identifier.replace('.', '_')}",
    )


def create_pkg(
    project: Project,
    env: Environment,
    *,
    name: str,
    version: str,
    identifier: str,
    sources: list[Target | FileNode | Path | str],
    install_location: str = "/Applications",
    output: str | Path | None = None,
    title: str | None = None,
    welcome: Path | None = None,
    readme: Path | None = None,
    license: Path | None = None,
    conclusion: Path | None = None,
    background: Path | None = None,
    min_os_version: str | None = None,
    scripts_dir: Path | None = None,
    sign_identity: str | None = None,
) -> Target:
    """Create a macOS product archive (.pkg) using productbuild.

    Product archives are full-featured installers with UI customization,
    license agreements, and multiple component packages.

    Args:
        project: Pcons project.
        env: Configured environment.
        name: Application/package name.
        version: Package version string.
        identifier: Bundle identifier (e.g., "com.example.myapp").
        sources: Files or directories to include (Targets or paths).
            Directory sources are automatically detected and copied with
            depfile tracking after resolve().
        install_location: Where files install (e.g., "/Applications").
        output: Output .pkg path. Defaults to build/<name>-<version>.pkg.
        title: Installer title. Defaults to name.
        welcome: Path to welcome.rtf or welcome.html.
        readme: Path to readme file.
        license: Path to license file.
        conclusion: Path to conclusion file.
        background: Path to background image.
        min_os_version: Minimum macOS version (e.g., "10.13").
        scripts_dir: Directory containing preinstall/postinstall scripts.
        sign_identity: Code signing identity.

    Returns:
        Target representing the .pkg file.

    Raises:
        ToolNotFoundError: If pkgbuild or productbuild is not found.
        ValueError: If staging path conflicts with existing build outputs.
    """
    check_tool("pkgbuild", "Install Xcode Command Line Tools: xcode-select --install")
    check_tool(
        "productbuild", "Install Xcode Command Line Tools: xcode-select --install"
    )

    python_cmd = sys.executable.replace("\\", "/")

    if output is None:
        output = Path(f"{name}-{version}.pkg")
    else:
        output = Path(output)

    title = title or name

    # Validate staging path doesn't conflict with user outputs
    _validate_staging_path(project, ".pkg_staging")

    # Set up staging directories (all paths relative to build_dir)
    staging_base_rel = Path(".pkg_staging") / name
    payload_rel = staging_base_rel / "payload"
    pkg_rel = staging_base_rel / "packages"
    resources_rel = staging_base_rel / "resources"

    # Stage source files (Install auto-detects directory sources after resolve)
    stage_target = project.Install(payload_rel, sources)

    # Check if any source is a .app bundle (needs component plist)
    def is_bundle_source(src: Target | FileNode | Path | str) -> bool:
        if hasattr(src, "output_name") and src.output_name:
            return str(src.output_name).endswith(".app")
        if hasattr(src, "name"):
            return str(src.name).endswith(".app")
        return str(src).endswith(".app")

    has_bundle = any(is_bundle_source(src) for src in sources)

    # Create component package with pkgbuild
    component_pkg_path = pkg_rel / f"{name}.pkg"
    pkgbuild_args = [
        "pkgbuild",
        "--root",
        str(payload_rel),
        "--identifier",
        identifier,
        "--version",
        version,
        "--install-location",
        install_location,
        "--ownership",
        "recommended",
    ]

    # Only use component plist for bundle sources (.app)
    # Non-bundle files (CLI tools, libraries) don't need it
    component_deps: list[Target] = [stage_target]
    if has_bundle:
        component_plist_path = staging_base_rel / "component.plist"
        plist_target = env.Command(
            target=component_plist_path,
            source=None,
            command=[
                python_cmd,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_plist",
                "--output",
                str(component_plist_path),
            ],
            name=f"plist_{name}",
        )
        pkgbuild_args.extend(["--component-plist", str(component_plist_path)])
        component_deps.append(plist_target)

    if scripts_dir is not None:
        pkgbuild_args.extend(["--scripts", str(scripts_dir)])

    pkgbuild_args.append(str(component_pkg_path))

    # Pass Targets directly as sources
    component_target = env.Command(
        target=component_pkg_path,
        source=component_deps,
        command=pkgbuild_args,
        name=f"component_{name}",
    )

    # Generate distribution.xml
    dist_xml_path = staging_base_rel / "distribution.xml"
    dist_cmd = [
        python_cmd,
        "-m",
        "pcons.contrib.installers._helpers",
        "gen_distribution",
        "--output",
        str(dist_xml_path),
        "--title",
        title,
        "--identifier",
        identifier,
        "--version",
        version,
        "--package",
        f"{name}.pkg",  # Can be repeated for multiple packages
    ]

    if min_os_version:
        dist_cmd.extend(["--min-os-version", min_os_version])

    dist_target = env.Command(
        target=dist_xml_path,
        source=[component_target],
        command=dist_cmd,
        name=f"distribution_{name}",
    )

    # Collect all targets that productbuild depends on
    productbuild_deps: list[Target] = [dist_target, component_target]

    # Copy resource files if provided
    for res_file in [welcome, readme, license, conclusion, background]:
        if res_file is not None:
            res_target = project.Install(resources_rel, [res_file])
            productbuild_deps.append(res_target)

    # Build final package with productbuild
    productbuild_args = [
        "productbuild",
        "--distribution",
        str(dist_xml_path),
        "--package-path",
        str(pkg_rel),
    ]

    if any(f is not None for f in [welcome, readme, license, conclusion, background]):
        productbuild_args.extend(["--resources", str(resources_rel)])

    if sign_identity is not None:
        productbuild_args.extend(["--sign", sign_identity])

    productbuild_args.append(str(output))

    return env.Command(
        target=output,
        source=productbuild_deps,
        command=productbuild_args,
        name=f"pkg_{name}",
    )


def create_dmg(
    project: Project,
    env: Environment,
    *,
    name: str,
    sources: list[Target | FileNode | Path | str],
    volume_name: str | None = None,
    output: str | Path | None = None,
    format: str = "UDZO",
    applications_symlink: bool = True,
) -> Target:
    """Create a macOS .dmg disk image using hdiutil.

    Creates a compressed disk image containing the specified files.
    Optionally includes a symlink to /Applications for drag-and-drop
    installation.

    Args:
        project: Pcons project.
        env: Configured environment.
        name: Application name (used for volume name and output).
        sources: Files or directories to include (Targets or paths).
            Directory sources are automatically detected and copied with
            depfile tracking after resolve().
        volume_name: Volume name. Defaults to name.
        output: Output .dmg path. Defaults to build/<name>.dmg.
        format: DMG format:
            - "UDZO" - zlib compressed (default, good compatibility)
            - "UDBZ" - bzip2 compressed (smaller, slower)
            - "ULFO" - lzfse compressed (macOS 10.11+, best compression)
            - "UDRO" - read-only, uncompressed
        applications_symlink: If True, add /Applications symlink for drag-install.

    Returns:
        Target representing the .dmg file.

    Raises:
        ToolNotFoundError: If hdiutil is not found.
        ValueError: If staging path conflicts with existing build outputs.
    """
    check_tool("hdiutil", "hdiutil should be available on macOS")

    volume_name = volume_name or name
    if output is None:
        output = Path(f"{name}.dmg")
    else:
        output = Path(output)

    # Validate staging path doesn't conflict with user outputs
    _validate_staging_path(project, ".dmg_staging")

    # Stage files to a temporary directory (path relative to build_dir)
    staging_rel = Path(".dmg_staging") / name

    # Stage source files (Install auto-detects directory sources after resolve)
    stage_target = project.Install(staging_rel, sources)

    # Build hdiutil command (with optional symlink creation)
    # Paths are relative to build_dir where ninja/make run
    if applications_symlink:
        hdiutil_cmd = [
            "bash",
            "-c",
            f'rm -f "{staging_rel}/Applications" && '
            f'ln -sf /Applications "{staging_rel}/Applications" && '
            f'rm -f "{output}" && '
            f'hdiutil create -volname "{volume_name}" '
            f'-srcfolder "{staging_rel}" -format {format} -ov "{output}"',
        ]
    else:
        hdiutil_cmd = [
            "bash",
            "-c",
            f'rm -f "{output}" && '
            f'hdiutil create -volname "{volume_name}" '
            f'-srcfolder "{staging_rel}" -format {format} -ov "{output}"',
        ]

    return env.Command(
        target=output,
        source=[stage_target],
        command=hdiutil_cmd,
        name=f"dmg_{name}",
    )


def sign_pkg(pkg_path: Path, identity: str) -> list[str]:
    """Return command to sign a package with productsign.

    Note: This returns the command rather than executing it, so it can
    be integrated into the build system.

    Args:
        pkg_path: Path to the package to sign.
        identity: Signing identity (e.g., "Developer ID Installer: Name").

    Returns:
        Command list for productsign.
    """
    check_tool(
        "productsign", "Install Xcode Command Line Tools: xcode-select --install"
    )

    output_path = pkg_path.with_suffix(".signed.pkg")
    return [
        "productsign",
        "--sign",
        identity,
        str(pkg_path),
        str(output_path),
    ]


def notarize_cmd(
    pkg_path: Path,
    *,
    apple_id: str,
    team_id: str,
    password_keychain_item: str | None = None,
) -> list[str]:
    """Return command to notarize and staple a package.

    Note: This returns the command rather than executing it. The password
    should be stored in the keychain using:
        xcrun notarytool store-credentials "notarytool-profile" \\
            --apple-id "your@email.com" \\
            --team-id "TEAM123" \\
            --password "app-specific-password"

    Args:
        pkg_path: Path to the package to notarize.
        apple_id: Apple ID email.
        team_id: Team ID.
        password_keychain_item: Keychain profile name (from store-credentials).

    Returns:
        Command list for notarization.
    """
    check_tool("xcrun", "Install Xcode Command Line Tools: xcode-select --install")

    if password_keychain_item:
        return [
            "bash",
            "-c",
            f'xcrun notarytool submit "{pkg_path}" '
            f"--keychain-profile {password_keychain_item} --wait && "
            f'xcrun stapler staple "{pkg_path}"',
        ]
    else:
        return [
            "bash",
            "-c",
            f'xcrun notarytool submit "{pkg_path}" '
            f'--apple-id "{apple_id}" --team-id "{team_id}" --wait && '
            f'xcrun stapler staple "{pkg_path}"',
        ]
