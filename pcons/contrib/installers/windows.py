# SPDX-License-Identifier: MIT
"""Windows installer creation helpers.

This module provides functions for creating Windows installers:
- create_msix(): Create .msix packages using MakeAppx.exe

These functions integrate with the pcons build system, generating proper
ninja rules with dependencies for incremental builds.

Requirements:
    - MakeAppx.exe (included with Windows SDK)
    - SignTool.exe for signing (included with Windows SDK)

Example:
    from pcons.contrib.installers import windows

    # Create an MSIX package
    msix = windows.create_msix(
        project, env,
        name="MyApp",
        version="1.0.0",
        publisher="CN=Example Corp",
        sources=[app_exe],
    )
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.contrib.installers._helpers import ToolNotFoundError

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


def _find_sdk_tool(tool_name: str) -> str | None:
    """Find a Windows SDK tool by searching common locations.

    Args:
        tool_name: Name of the tool (e.g., "MakeAppx.exe").

    Returns:
        Path to the tool, or None if not found.
    """
    import shutil

    # First check if it's in PATH
    path = shutil.which(tool_name)
    if path:
        return path

    # Search in Windows SDK locations
    sdk_roots = [
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
        Path(r"C:\Program Files\Windows Kits\10\bin"),
    ]

    for sdk_root in sdk_roots:
        if not sdk_root.exists():
            continue
        # Search for the tool in version subdirectories
        for version_dir in sorted(sdk_root.iterdir(), reverse=True):
            if not version_dir.is_dir():
                continue
            # Check x64 first, then x86
            for arch in ["x64", "x86", "arm64"]:
                tool_path = version_dir / arch / tool_name
                if tool_path.exists():
                    return str(tool_path)

    return None


def create_msix(
    project: Project,
    env: Environment,
    *,
    name: str,
    version: str,
    publisher: str,
    sources: list[Target | FileNode | Path | str],
    executable: str | None = None,
    output: str | Path | None = None,
    display_name: str | None = None,
    description: str | None = None,
    processor_architecture: str = "x64",
    sign_cert: Path | None = None,
    sign_password: str | None = None,
) -> Target:
    """Create a Windows MSIX package using MakeAppx.exe.

    MSIX is the modern Windows packaging format, replacing both .appx
    and traditional installers for many scenarios.

    Args:
        project: Pcons project.
        env: Configured environment.
        name: Package name (alphanumeric, no spaces).
        version: Package version (X.Y.Z.W format recommended).
        publisher: Publisher identity (e.g., "CN=Example Corp").
        sources: Files or directories to include (Targets or paths).
            Directory sources are automatically detected and copied with
            depfile tracking after resolve().
        executable: Name of the main executable (e.g., "myapp.exe").
            If not specified, defaults to first source file's name.
        output: Output .msix path. Defaults to build/<name>-<version>.msix.
        display_name: Display name shown to users. Defaults to name.
        description: Package description.
        processor_architecture: Target architecture ("x64", "x86", "arm64").
        sign_cert: Path to .pfx certificate for signing.
        sign_password: Password for the certificate.

    Returns:
        Target representing the .msix file.

    Raises:
        ToolNotFoundError: If MakeAppx.exe is not found.
    """
    makeappx = _find_sdk_tool("MakeAppx.exe")
    if makeappx is None:
        raise ToolNotFoundError(
            "MakeAppx.exe",
            "Install Windows SDK: https://developer.microsoft.com/windows/downloads/windows-sdk/",
        )

    python_cmd = sys.executable.replace("\\", "/")

    if output is None:
        output = Path(f"{name}-{version}.msix")
    else:
        output = Path(output)

    # Derive executable name from first source if not specified
    if executable is None:
        first_source = sources[0] if sources else None
        if first_source is not None:
            # Handle Target, Path, or str
            if hasattr(first_source, "output_name") and first_source.output_name:
                executable = str(first_source.output_name)
            elif hasattr(first_source, "name") and first_source.name:
                executable = str(first_source.name)
            elif isinstance(first_source, Path):
                executable = first_source.name
            elif isinstance(first_source, str):
                executable = first_source.split("/")[-1].split("\\")[-1]
        # Fallback to name-based executable
        if not executable:
            executable = f"{name}.exe"
    # At this point executable is guaranteed to be set
    assert executable is not None
    # Ensure executable has .exe extension
    if not executable.lower().endswith(".exe"):
        executable = f"{executable}.exe"

    # Set up staging directory (use relative paths for commands)
    staging_rel = Path(".msix_staging") / name
    manifest_rel = staging_rel / "AppxManifest.xml"

    # Stage source files (Install auto-detects directory sources after resolve)
    stage_target = project.Install(staging_rel, sources)

    # Generate AppxManifest.xml (use relative path for target)
    manifest_target = env.Command(
        target=manifest_rel,
        source=None,
        command=[
            python_cmd,
            "-m",
            "pcons.contrib.installers._helpers",
            "gen_appx_manifest",
            "--output",
            str(manifest_rel),
            "--name",
            name,
            "--version",
            version,
            "--publisher",
            publisher,
            "--executable",
            executable,
            *(["--display-name", display_name] if display_name else []),
            *(["--description", description] if description else []),
        ],
        name=f"manifest_{name}",
    )

    # Generate placeholder assets (required for MSIX)
    # Output a stamp file to track that assets were generated
    assets_stamp = staging_rel / "Assets" / ".stamp"
    assets_target = env.Command(
        target=assets_stamp,
        source=None,
        command=[
            python_cmd,
            "-m",
            "pcons.contrib.installers._helpers",
            "gen_msix_assets",
            "--output-dir",
            str(staging_rel),
        ],
        name=f"assets_{name}",
    )

    # Build MSIX with MakeAppx (use relative path for staging dir)
    makeappx_cmd = [
        makeappx,
        "pack",
        "/d",
        str(staging_rel),
        "/p",
        str(output),
        "/o",  # Overwrite existing
    ]

    msix_target = env.Command(
        target=output,
        source=[stage_target, manifest_target, assets_target],
        command=makeappx_cmd,
        name=f"msix_{name}",
    )

    # Sign if certificate provided
    if sign_cert is not None:
        signtool = _find_sdk_tool("SignTool.exe")
        if signtool is None:
            raise ToolNotFoundError(
                "SignTool.exe",
                "Install Windows SDK for code signing support",
            )

        sign_cmd = [
            signtool,
            "sign",
            "/fd",
            "SHA256",
            "/f",
            str(sign_cert),
        ]
        if sign_password:
            sign_cmd.extend(["/p", sign_password])
        sign_cmd.append(str(output))

        signed_target = env.Command(
            target=output.with_suffix(".signed.msix"),
            source=[msix_target],
            command=sign_cmd,
            name=f"sign_{name}",
        )
        return signed_target

    return msix_target


def create_appx(
    project: Project,
    env: Environment,
    *,
    name: str,
    version: str,
    publisher: str,
    sources: list[Target | FileNode | Path | str],
    output: str | Path | None = None,
    display_name: str | None = None,
    description: str | None = None,
    processor_architecture: str = "x64",
) -> Target:
    """Create a Windows AppX package (legacy format).

    This is an alias for create_msix() as the tooling is identical.
    MSIX is the recommended format for new applications.

    Args:
        See create_msix() for argument documentation.

    Returns:
        Target representing the .appx file.
    """
    if output is None:
        output = Path(f"{name}-{version}.appx")

    return create_msix(
        project,
        env,
        name=name,
        version=version,
        publisher=publisher,
        sources=sources,
        output=output,
        display_name=display_name,
        description=description,
        processor_architecture=processor_architecture,
    )
