# SPDX-License-Identifier: MIT
"""Shared helper utilities for installer generation.

This module provides:
- Staging utilities for preparing files for packaging
- Metadata generators (component plist, distribution.xml, AppxManifest)
- Common error classes

These helpers can be invoked as subprocesses via `python -m pcons.contrib.installers._helpers`
for proper ninja integration, or called directly from Python.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


class InstallerError(Exception):
    """Base exception for installer-related errors."""


class ToolNotFoundError(InstallerError):
    """Required installer tool was not found."""

    def __init__(self, tool: str, hint: str | None = None) -> None:
        self.tool = tool
        self.hint = hint
        msg = f"Installer tool not found: {tool}"
        if hint:
            msg += f"\n  {hint}"
        super().__init__(msg)


def check_tool(tool: str, hint: str | None = None) -> str:
    """Check if a tool exists and return its path.

    Args:
        tool: Name of the tool to find.
        hint: Optional installation hint if not found.

    Returns:
        Path to the tool.

    Raises:
        ToolNotFoundError: If the tool is not found.
    """
    path = shutil.which(tool)
    if path is None:
        raise ToolNotFoundError(tool, hint)
    return path


def stage_files(
    project: Project,
    env: Environment,  # noqa: ARG001 - kept for API consistency
    sources: list[Target | FileNode | Path | str],
    staging_dir: Path,
    install_prefix: str = "",
) -> Target:
    """Create a target to stage files for packaging.

    This creates an Install target that copies source files to a staging
    directory, preserving their basenames.

    Args:
        project: Pcons project.
        env: Configured environment.
        sources: Files to stage (Targets, FileNodes, or paths).
        staging_dir: Directory to stage files to.
        install_prefix: Optional subdirectory within staging_dir.

    Returns:
        Target representing the staging operation.
    """
    dest_dir = staging_dir / install_prefix if install_prefix else staging_dir
    return project.Install(dest_dir, sources)


def generate_component_plist(
    output: Path,
    *,
    relocatable: bool = False,
    version_checked: bool = True,
    overwrite_action: str = "upgrade",
) -> None:
    """Generate a macOS component property list file.

    Component plists control bundle-specific behavior during installation.

    Args:
        output: Path to write the plist file.
        relocatable: If True, installer follows if user moved the app.
        version_checked: If True, check version before upgrade.
        overwrite_action: Action when bundle exists ("upgrade" or "update").
    """
    plist_data = {
        "BundleIsRelocatable": relocatable,
        "BundleIsVersionChecked": version_checked,
        "BundleOverwriteAction": overwrite_action,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        plistlib.dump([plist_data], f)


def generate_distribution_xml(
    output: Path,
    *,
    title: str,
    identifier: str,
    version: str,
    packages: list[str],
    min_os_version: str | None = None,
    welcome: Path | None = None,
    readme: Path | None = None,
    license: Path | None = None,
    background: Path | None = None,
    host_architectures: str = "x86_64,arm64",
) -> None:
    """Generate a macOS distribution.xml file for productbuild.

    The distribution.xml controls the installer UI and package selection.

    Args:
        output: Path to write the XML file.
        title: Installer title shown to user.
        identifier: Package identifier (e.g., "com.example.myapp").
        version: Package version string.
        packages: List of component package filenames.
        min_os_version: Minimum macOS version (e.g., "10.13").
        welcome: Path to welcome.rtf or welcome.html.
        readme: Path to readme file.
        license: Path to license file.
        background: Path to background image.
        host_architectures: Supported architectures (comma-separated).
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<installer-gui-script minSpecVersion="2">')
    lines.append(f"    <title>{_xml_escape(title)}</title>")
    lines.append(
        f'    <options customize="never" hostArchitectures="{host_architectures}"/>'
    )

    if min_os_version:
        lines.append("    <allowed-os-versions>")
        lines.append(f'        <os-version min="{min_os_version}"/>')
        lines.append("    </allowed-os-versions>")

    if welcome:
        lines.append(f'    <welcome file="{welcome.name}"/>')
    if readme:
        lines.append(f'    <readme file="{readme.name}"/>')
    if license:
        lines.append(f'    <license file="{license.name}"/>')
    if background:
        lines.append(
            f'    <background file="{background.name}" alignment="bottomleft"/>'
        )

    # Choices and package references
    lines.append("    <choices-outline>")
    lines.append('        <line choice="default"/>')
    lines.append("    </choices-outline>")

    lines.append(f'    <choice id="default" title="{_xml_escape(title)}">')
    for _pkg in packages:
        pkg_id = identifier  # For simplicity, use main identifier
        lines.append(f'        <pkg-ref id="{pkg_id}"/>')
    lines.append("    </choice>")

    for pkg in packages:
        pkg_id = identifier
        lines.append(f'    <pkg-ref id="{pkg_id}" version="{version}">{pkg}</pkg-ref>')

    lines.append("</installer-gui-script>")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines))


def generate_appx_manifest(
    output: Path,
    *,
    name: str,
    version: str,
    publisher: str,
    executable: str | None = None,
    display_name: str | None = None,
    description: str | None = None,
    processor_architecture: str = "x64",
) -> None:
    """Generate a Windows AppxManifest.xml file for MSIX packaging.

    Args:
        output: Path to write the manifest file.
        name: Package name (no spaces, alphanumeric).
        version: Version in X.Y.Z.W format.
        publisher: Publisher identity (e.g., "CN=Example Corp").
        executable: Main executable name (e.g., "myapp.exe").
        display_name: Display name shown to users.
        description: Package description.
        processor_architecture: Target architecture ("x64", "x86", "arm64").
    """
    display_name = display_name or name
    description = description or f"{name} application"
    executable = executable or f"{name}.exe"

    # Ensure version has 4 components
    version_parts = version.split(".")
    while len(version_parts) < 4:
        version_parts.append("0")
    version = ".".join(version_parts[:4])

    manifest = f"""\
<?xml version="1.0" encoding="utf-8"?>
<Package
    xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
    xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
    xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">

    <Identity
        Name="{_xml_escape(name)}"
        Publisher="{_xml_escape(publisher)}"
        Version="{version}"
        ProcessorArchitecture="{processor_architecture}"/>

    <Properties>
        <DisplayName>{_xml_escape(display_name)}</DisplayName>
        <PublisherDisplayName>{_xml_escape(publisher)}</PublisherDisplayName>
        <Description>{_xml_escape(description)}</Description>
        <Logo>Assets\\StoreLogo.png</Logo>
    </Properties>

    <Resources>
        <Resource Language="en-us"/>
    </Resources>

    <Dependencies>
        <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.22000.0"/>
    </Dependencies>

    <Applications>
        <Application Id="App" Executable="{executable}" EntryPoint="Windows.FullTrustApplication">
            <uap:VisualElements
                DisplayName="{_xml_escape(display_name)}"
                Description="{_xml_escape(description)}"
                BackgroundColor="transparent"
                Square150x150Logo="Assets\\Square150x150Logo.png"
                Square44x44Logo="Assets\\Square44x44Logo.png">
            </uap:VisualElements>
        </Application>
    </Applications>

    <Capabilities>
        <rescap:Capability Name="runFullTrust"/>
    </Capabilities>
</Package>
"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest)


def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def generate_msix_assets(output_dir: Path) -> None:
    """Generate placeholder PNG assets for MSIX packaging.

    Creates minimal 1x1 transparent PNG files that satisfy MSIX requirements.
    For production use, replace these with proper app icons.

    Also creates a .stamp file to serve as a build target.

    Args:
        output_dir: Directory to create Assets folder in.
    """
    import base64

    # Minimal 1x1 transparent PNG (67 bytes)
    # This is a valid PNG that MakeAppx will accept
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
        "YGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
    )
    png_data = base64.b64decode(png_b64)

    assets_dir = output_dir / "Assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Create required asset files
    asset_files = [
        "StoreLogo.png",
        "Square150x150Logo.png",
        "Square44x44Logo.png",
    ]

    for asset_file in asset_files:
        (assets_dir / asset_file).write_bytes(png_data)

    # Create stamp file to track that assets were generated
    (assets_dir / ".stamp").write_text("assets generated")


# CLI entry point for subprocess invocation
def main() -> int:
    """Command-line interface for helper functions.

    This allows the helpers to be invoked as subprocesses:
        python -m pcons.contrib.installers._helpers gen_plist --output foo.plist
    """
    parser = argparse.ArgumentParser(description="Installer helper utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # gen_plist command
    plist_parser = subparsers.add_parser(
        "gen_plist", help="Generate component property list"
    )
    plist_parser.add_argument("--output", "-o", required=True, help="Output path")
    plist_parser.add_argument(
        "--relocatable", action="store_true", help="Bundle is relocatable"
    )
    plist_parser.add_argument(
        "--no-version-check", action="store_true", help="Don't check version"
    )
    plist_parser.add_argument(
        "--overwrite-action", default="upgrade", help="Overwrite action"
    )

    # gen_distribution command
    dist_parser = subparsers.add_parser(
        "gen_distribution", help="Generate distribution.xml"
    )
    dist_parser.add_argument("--output", "-o", required=True, help="Output path")
    dist_parser.add_argument("--title", required=True, help="Installer title")
    dist_parser.add_argument("--identifier", required=True, help="Package identifier")
    dist_parser.add_argument("--version", required=True, help="Package version")
    dist_parser.add_argument(
        "--package",
        action="append",
        required=True,
        dest="packages",
        help="Component package filename (can be repeated)",
    )
    dist_parser.add_argument("--min-os-version", help="Minimum macOS version")
    dist_parser.add_argument("--welcome", help="Path to welcome file")
    dist_parser.add_argument("--readme", help="Path to readme file")
    dist_parser.add_argument("--license", help="Path to license file")
    dist_parser.add_argument("--background", help="Path to background image")

    # gen_appx_manifest command
    appx_parser = subparsers.add_parser(
        "gen_appx_manifest", help="Generate AppxManifest.xml"
    )
    appx_parser.add_argument("--output", "-o", required=True, help="Output path")
    appx_parser.add_argument("--name", required=True, help="Package name")
    appx_parser.add_argument("--version", required=True, help="Package version")
    appx_parser.add_argument("--publisher", required=True, help="Publisher identity")
    appx_parser.add_argument(
        "--executable", help="Main executable name (e.g., myapp.exe)"
    )
    appx_parser.add_argument("--display-name", help="Display name")
    appx_parser.add_argument("--description", help="Package description")

    # gen_msix_assets command
    assets_parser = subparsers.add_parser(
        "gen_msix_assets", help="Generate placeholder MSIX assets"
    )
    assets_parser.add_argument(
        "--output-dir", "-o", required=True, help="Output directory"
    )

    args = parser.parse_args()

    if args.command == "gen_plist":
        generate_component_plist(
            Path(args.output),
            relocatable=args.relocatable,
            version_checked=not args.no_version_check,
            overwrite_action=args.overwrite_action,
        )
    elif args.command == "gen_distribution":
        generate_distribution_xml(
            Path(args.output),
            title=args.title,
            identifier=args.identifier,
            version=args.version,
            packages=args.packages,  # Already a list from --package append action
            min_os_version=args.min_os_version,
            welcome=Path(args.welcome) if args.welcome else None,
            readme=Path(args.readme) if args.readme else None,
            license=Path(args.license) if args.license else None,
            background=Path(args.background) if args.background else None,
        )
    elif args.command == "gen_appx_manifest":
        generate_appx_manifest(
            Path(args.output),
            name=args.name,
            version=args.version,
            publisher=args.publisher,
            executable=args.executable,
            display_name=args.display_name,
            description=args.description,
        )
    elif args.command == "gen_msix_assets":
        generate_msix_assets(Path(args.output_dir))

    return 0


if __name__ == "__main__":
    sys.exit(main())
