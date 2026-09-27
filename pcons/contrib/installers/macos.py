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
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.contrib.installers._helpers import (
    as_installer_step,
    staging_dir,
)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


def _check_tool(tool: str, hint: str | None = None) -> str:
    # Deferred import: importing _helpers at module scope triggers a runpy
    # "found in sys.modules after import of package" RuntimeWarning when a
    # build script is run via `python -m`.
    from pcons.contrib.installers._helpers import check_tool

    return check_tool(tool, hint)


# Reserved staging directory prefixes for installer generation.
# These are used in the build directory and should not conflict with user outputs.
_RESERVED_STAGING_PREFIXES = frozenset(
    {".pkg_staging", ".dmg_staging", ".msix_staging"}
)


def _validate_staging_path(project: Project, staging_dir: Path | str) -> None:
    """Validate that an installer's staging directory is unused.

    Checks that no existing targets or nodes in the project have paths
    under the staging directory. This prevents accidental overwrites and
    ensures installer staging is isolated from user outputs. Each
    installer stages under its own directory, so several can share one
    project.

    Args:
        project: The project to check for conflicts.
        staging_dir: The installer's staging directory, as a node path
            (see :func:`staging_dir`).

    Raises:
        ValueError: If a conflict is detected with existing build outputs.
    """
    staging_path = Path(staging_dir)

    # Check for conflicts with existing targets' output nodes
    for target in project.targets:
        for node in target.output_nodes:
            node_path = node.path
            if node_path.is_relative_to(staging_path):
                raise ValueError(
                    f"Installer staging path '{staging_path}' conflicts with "
                    f"target '{target.name}' output: {node_path}. "
                    f"Rename the target's output or use a different build directory."
                )

    # Check for conflicts with existing nodes in project
    for node_path in project._nodes:
        if node_path.is_relative_to(staging_path):
            raise ValueError(
                f"Installer staging path '{staging_path}' conflicts with "
                f"existing build node: {node_path}. "
                f"This may indicate a naming conflict in your build configuration."
            )


def create_component_pkg(
    project: Project,
    env: Environment,
    *,
    identifier: str,
    version: str,
    sources: Sequence[Target | FileNode | Path | str],
    install_location: str = "/Applications",
    output: str | Path | None = None,
    depends: Sequence[Target] | None = None,
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
        output: Output .pkg path, written like ``target=``. Defaults to
            <identifier>-<version>.pkg in the build directory.
        depends: Targets that must be built before the sources are staged,
            for a directory source that other targets populate.
        scripts_dir: Directory containing preinstall/postinstall scripts,
            read from the build script's directory.
        component_plist: Path to component plist file for bundle settings,
            read from the build script's directory.
        ownership: File ownership ("recommended", "preserve", "preserve-other").
        sign_identity: Code signing identity (e.g., "Developer ID Installer: Name").

    Returns:
        Target representing the .pkg file.

    Raises:
        ToolNotFoundError: If pkgbuild is not found.
        ValueError: If staging path conflicts with existing build outputs.
    """
    _check_tool("pkgbuild", "Install Xcode Command Line Tools: xcode-select --install")

    if output is None:
        output = Path(f"{identifier}-{version}.pkg")
    else:
        output = Path(output)

    # Absolute, so the path means the same in target= and in a command.
    output = env.build_dir / output
    staging_base = staging_dir(env, "pkg", identifier)
    _validate_staging_path(project, staging_base)
    payload = staging_base / "payload"

    stage_target = project.Install(payload, sources, no_prefix=True, env=env)
    if depends:
        stage_target.depends(*depends)

    pkgbuild_args = [
        "pkgbuild",
        "--root",
        payload,
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
        pkgbuild_args.extend(["--scripts", project.current_dir / scripts_dir])

    if component_plist is not None:
        pkgbuild_args.extend(
            ["--component-plist", project.current_dir / component_plist]
        )

    if sign_identity is not None:
        pkgbuild_args.extend(["--sign", sign_identity])

    pkgbuild_args.append("$TARGET")

    return as_installer_step(
        env.Command(target=output, source=[stage_target], command=pkgbuild_args),
        by="create_component_pkg",
    )


def create_pkg(
    project: Project,
    env: Environment,
    *,
    name: str,
    version: str,
    identifier: str,
    sources: Sequence[Target | FileNode | Path | str],
    install_location: str = "/Applications",
    output: str | Path | None = None,
    depends: Sequence[Target] | None = None,
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
        output: Output .pkg path, written like ``target=``. Defaults to
            <name>-<version>.pkg in the build directory.
        depends: Targets that must be built before the sources are staged,
            for a directory source that other targets populate.
        title: Installer title. Defaults to name.
        welcome: Welcome page shown first (.txt, .rtf or .html).
        readme: Readme page (.txt, .rtf or .html).
        license: License the user must accept (.txt, .rtf or .html).
        conclusion: Page shown when the install finishes.
        background: Background image for the installer window.
        min_os_version: Minimum macOS version (e.g., "10.13").
        scripts_dir: Directory containing preinstall/postinstall scripts,
            read from the build script's directory.
        sign_identity: Code signing identity.

    Returns:
        Target representing the .pkg file.

    Raises:
        ToolNotFoundError: If pkgbuild or productbuild is not found.
        ValueError: If staging path conflicts with existing build outputs.
    """
    _check_tool("pkgbuild", "Install Xcode Command Line Tools: xcode-select --install")
    _check_tool(
        "productbuild", "Install Xcode Command Line Tools: xcode-select --install"
    )

    python_cmd = sys.executable.replace("\\", "/")

    if output is None:
        output = Path(f"{name}-{version}.pkg")
    else:
        output = Path(output)

    title = title or name

    # Installer UI files, copied into the package's Resources directory and
    # referenced there by basename from distribution.xml.
    ui_resources = {
        flag: path
        for flag, path in (
            ("welcome", welcome),
            ("readme", readme),
            ("license", license),
            ("conclusion", conclusion),
            ("background", background),
        )
        if path is not None
    }

    # Absolute, so the path means the same in target= and in a command.
    output = env.build_dir / output
    staging_base = staging_dir(env, "pkg", name)
    _validate_staging_path(project, staging_base)
    payload = staging_base / "payload"
    packages = staging_base / "packages"
    resources = staging_base / "resources"

    stage_target = project.Install(payload, sources, no_prefix=True, env=env)
    if depends:
        stage_target.depends(*depends)

    # Bundle sources (.app) need a component plist; pkgbuild requires
    # each bundle's payload-relative path in it.
    def bundle_name(src: Target | FileNode | Path | str) -> str | None:
        if named := getattr(src, "output_filename", None):
            name_str = str(named)
        elif hasattr(src, "output_name") and src.output_name:
            name_str = str(src.output_name)
        elif hasattr(src, "name"):
            name_str = str(src.name)
        else:
            name_str = Path(str(src)).name
        return name_str if name_str.endswith(".app") else None

    bundle_names = [b for b in (bundle_name(src) for src in sources) if b]

    # Create component package with pkgbuild
    pkgbuild_args = [
        "pkgbuild",
        "--root",
        payload,
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
    if bundle_names:
        component_plist = staging_base / "component.plist"
        bundle_args: list[str] = []
        for bundle in bundle_names:
            bundle_args.extend(["--bundle", bundle])
        plist_target = as_installer_step(
            env.Command(
                target=component_plist,
                source=None,
                command=[
                    python_cmd,
                    "-m",
                    "pcons.contrib.installers._helpers",
                    "gen_plist",
                    "--output",
                    "$TARGET",
                    *bundle_args,
                ],
            ),
            by="create_pkg",
        )
        pkgbuild_args.extend(["--component-plist", component_plist])
        component_deps.append(plist_target)

    if scripts_dir is not None:
        pkgbuild_args.extend(["--scripts", project.current_dir / scripts_dir])

    pkgbuild_args.append("$TARGET")

    # Pass Targets directly as sources
    component_target = as_installer_step(
        env.Command(
            target=packages / f"{name}.pkg",
            source=component_deps,
            command=pkgbuild_args,
        ),
        by="create_pkg",
    )

    # Generate distribution.xml
    dist_xml = staging_base / "distribution.xml"
    dist_cmd = [
        python_cmd,
        "-m",
        "pcons.contrib.installers._helpers",
        "gen_distribution",
        "--output",
        "$TARGET",
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

    for flag, path in ui_resources.items():
        dist_cmd.extend([f"--{flag}", Path(path).name])

    dist_target = as_installer_step(
        env.Command(target=dist_xml, source=[component_target], command=dist_cmd),
        by="create_pkg",
    )

    # Collect all targets that productbuild depends on
    productbuild_deps: list[Target] = [dist_target, component_target]

    productbuild_deps.extend(
        project.Install(resources, [path], no_prefix=True, env=env)
        for path in ui_resources.values()
    )

    # Build final package with productbuild
    productbuild_args = [
        "productbuild",
        "--distribution",
        dist_xml,
        "--package-path",
        packages,
    ]

    if ui_resources:
        productbuild_args.extend(["--resources", resources])

    if sign_identity is not None:
        productbuild_args.extend(["--sign", sign_identity])

    productbuild_args.append("$TARGET")

    return as_installer_step(
        env.Command(target=output, source=productbuild_deps, command=productbuild_args),
        by="create_pkg",
    )


def create_dmg(
    project: Project,
    env: Environment,
    *,
    name: str,
    sources: Sequence[Target | FileNode | Path | str],
    volume_name: str | None = None,
    output: str | Path | None = None,
    depends: Sequence[Target] | None = None,
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
        output: Output .dmg path, written like ``target=``. Defaults to
            <name>.dmg in the build directory.
        depends: Targets that must be built before the sources are staged,
            for a directory source that other targets populate.
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
    _check_tool("hdiutil", "hdiutil should be available on macOS")

    volume_name = volume_name or name
    if output is None:
        output = Path(f"{name}.dmg")
    else:
        output = Path(output)

    output = env.build_dir / output
    staging = staging_dir(env, "dmg", name)
    _validate_staging_path(project, staging)

    stage_target = project.Install(staging, sources, no_prefix=True, env=env)
    if depends:
        stage_target.depends(*depends)

    # The paths go to the script as arguments ($1 the staging directory, $2
    # the image), so pcons writes and quotes them rather than this string.
    script = (
        'rm -f "$$1/Applications" && ln -sf /Applications "$$1/Applications" && '
        if applications_symlink
        else ""
    )
    script += (
        f'rm -f "$$2" && hdiutil create -volname "$$3" '
        f'-srcfolder "$$1" -format {format} -ov "$$2"'
    )
    hdiutil_cmd = ["bash", "-c", script, "bash", staging, "$TARGET", volume_name]

    return as_installer_step(
        env.Command(target=output, source=[stage_target], command=hdiutil_cmd),
        by="create_dmg",
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
    _check_tool(
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
    _check_tool("xcrun", "Install Xcode Command Line Tools: xcode-select --install")

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
