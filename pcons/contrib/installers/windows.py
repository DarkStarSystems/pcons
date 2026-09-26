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
    sources: Sequence[Target | FileNode | Path | str],
    executable: str | None = None,
    output: str | Path | None = None,
    depends: Sequence[Target] | None = None,
    display_name: str | None = None,
    description: str | None = None,
    processor_architecture: str = "x64",
    sign_cert: Path | None = None,
    sign_password_env: str | None = None,
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
        executable: Package-relative path of the main executable (e.g.,
            "myapp.exe"). A directory source stages as a subdirectory of
            the package root, so an exe inside one needs that prefix
            (e.g. sources=["build/deploy"] -> executable="deploy\\myapp.exe").
            If not specified, defaults to first source file's name.
        output: Output .msix path, written like ``target=``. Defaults to
            <name>-<version>.msix in the build directory.
        depends: Targets that must be built before the sources are staged,
            for a directory source that other targets populate.
        display_name: Display name shown to users. Defaults to name.
        description: Package description.
        processor_architecture: Target architecture ("x64", "x86", "arm64").
        sign_cert: Path to .pfx certificate for signing, read from the build
            script's directory.
        sign_password_env: Name of an environment variable holding the
            certificate password. The password itself is never embedded in
            the generated build file; it is read from the environment when
            the signing step actually runs. Leave unset for certificates
            that don't require a password.

    Returns:
        Target representing the .msix file.

    Raises:
        ToolNotFoundError: If MakeAppx.exe is not found.
    """
    makeappx = _find_sdk_tool("MakeAppx.exe")
    if makeappx is None:
        # Deferred import: avoids a runpy double-import RuntimeWarning when a
        # build script is run via `python -m` (see macos.py:_check_tool).
        from pcons.contrib.installers._helpers import ToolNotFoundError

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
            if named := getattr(first_source, "output_filename", None):
                executable = str(named)
            elif hasattr(first_source, "output_name") and first_source.output_name:
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

    # Absolute, so the path means the same in target= and in a command.
    output = env.build_dir / output
    staging = staging_dir(env, "msix", name)

    stage_target = project.Install(staging, sources, no_prefix=True, env=env)
    if depends:
        stage_target.depends(*depends)

    manifest_target = as_installer_step(
        env.Command(
            target=staging / "AppxManifest.xml",
            source=None,
            command=[
                python_cmd,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_appx_manifest",
                "--output",
                "$TARGET",
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
        ),
        by="create_msix",
    )

    # Generate placeholder assets (required for MSIX)
    # Output a stamp file to track that assets were generated
    assets_target = as_installer_step(
        env.Command(
            target=staging / "Assets" / ".stamp",
            source=None,
            command=[
                python_cmd,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_msix_assets",
                "--output-dir",
                staging,
            ],
        ),
        by="create_msix",
    )

    # Build MSIX with MakeAppx
    makeappx_cmd = [
        makeappx,
        "pack",
        "/d",
        staging,
        "/p",
        "$TARGET",
        "/o",  # Overwrite existing
    ]

    msix_target = as_installer_step(
        env.Command(
            target=output,
            source=[stage_target, manifest_target, assets_target],
            command=makeappx_cmd,
        ),
        by="create_msix",
    )

    # Sign if certificate provided
    if sign_cert is not None:
        signtool = _find_sdk_tool("SignTool.exe")
        if signtool is None:
            from pcons.contrib.installers._helpers import ToolNotFoundError

            raise ToolNotFoundError(
                "SignTool.exe",
                "Install Windows SDK for code signing support",
            )

        # SignTool signs its target in place rather than producing a new
        # file. Route through the _helpers CLI (like the manifest/assets
        # steps above) so the declared ninja target is the file that's
        # actually written: a copy of the unsigned .msix is made and that
        # copy is signed, leaving the unsigned package intact.
        sign_cmd = [
            python_cmd,
            "-m",
            "pcons.contrib.installers._helpers",
            "sign_msix",
            "--input",
            "$SOURCE",
            "--output",
            "$TARGET",
            "--signtool",
            signtool,
            "--cert",
            str(project.current_dir / sign_cert),
            *(["--password-env", sign_password_env] if sign_password_env else []),
        ]

        signed_target = as_installer_step(
            env.Command(
                target=output.with_suffix(".signed.msix"),
                source=[msix_target],
                command=sign_cmd,
            ),
            by="create_msix",
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
    sources: Sequence[Target | FileNode | Path | str],
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
