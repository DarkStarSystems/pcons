# SPDX-License-Identifier: MIT
"""Windows installer creation helpers.

This module provides functions for creating Windows installers:
- create_msix(): Create .msix packages using MakeAppx.exe
- create_inno_setup(): Compile an Inno Setup script into a setup .exe

These functions integrate with the pcons build system, generating proper
ninja rules with dependencies for incremental builds.

Requirements:
    - MakeAppx.exe (included with Windows SDK)
    - SignTool.exe for signing (included with Windows SDK)
    - ISCC.exe for Inno Setup (https://jrsoftware.org/isinfo.php)

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

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.contrib.installers._helpers import (
    MSIX_LOGOS,
    as_installer_step,
    staging_dir,
)
from pcons.core.errors import PconsValueError
from pcons.core.paths import last_component

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
    icon: Target | Path | str | None = None,
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
        icon: The package's icon, shown in the Start menu, the taskbar and
            the installer: a square ``.png``, or an ``.ico`` with a PNG
            entry (modern icons store their 256px image as one), whose
            largest PNG is used. 256px or larger looks best; Windows scales
            it down. A path is read from the build script's directory, and
            a Target that produces the image works too. Without one the
            package has a blank icon. The icon embedded in the ``.exe``
            itself comes from a ``.rc`` file (see examples/11).
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
                executable = first_source
            # Staged at the package root, by its file name alone
            if executable:
                executable = last_component(executable)
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
                "--processor-architecture",
                processor_architecture,
            ],
        ),
        by="create_msix",
    )

    logos_target = as_installer_step(
        env.Command(
            target=[staging / logo for logo in MSIX_LOGOS],
            source=[icon] if icon is not None else None,
            command=[
                python_cmd,
                "-m",
                "pcons.contrib.installers._helpers",
                "stage_msix_logos",
                "--package-dir",
                staging,
                *(["--icon", "$SOURCE"] if icon is not None else []),
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
            source=[stage_target, manifest_target, logos_target],
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
                target=output.with_suffix(f".signed{output.suffix}"),
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
    executable: str | None = None,
    output: str | Path | None = None,
    depends: Sequence[Target] | None = None,
    display_name: str | None = None,
    description: str | None = None,
    icon: Target | Path | str | None = None,
    processor_architecture: str = "x64",
    sign_cert: Path | None = None,
    sign_password_env: str | None = None,
) -> Target:
    """Create a Windows AppX package (legacy format).

    This is create_msix() with an ``.appx`` default output, as the tooling
    is identical. MSIX is the recommended format for new applications.

    Args:
        See create_msix() for argument documentation.

    Returns:
        Target representing the .appx file.
    """
    return create_msix(
        project,
        env,
        name=name,
        version=version,
        publisher=publisher,
        sources=sources,
        executable=executable,
        output=output if output is not None else f"{name}-{version}.appx",
        depends=depends,
        display_name=display_name,
        description=description,
        icon=icon,
        processor_architecture=processor_architecture,
        sign_cert=sign_cert,
        sign_password_env=sign_password_env,
    )


def _find_iscc() -> str | None:
    """Find Inno Setup's compiler, ISCC.exe: on PATH, else where its
    installer puts it, for all users or (as winget does) just one. The
    newest version wins."""
    import shutil

    if path := shutil.which("ISCC"):
        return path
    roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("LOCALAPPDATA") and Path(os.environ["LOCALAPPDATA"], "Programs"),
    ]
    for root in filter(None, roots):
        found = sorted(Path(root).glob("Inno Setup */ISCC.exe"), reverse=True)
        if found:
            return str(found[0])
    return None


def create_inno_setup(
    project: Project,
    env: Environment,
    *,
    name: str,
    script: Path | str,
    sources: Sequence[Target | FileNode | Path | str] = (),
    defines: Mapping[str, str | Path | Target | FileNode] | None = None,
    output: str | Path | None = None,
    depends: Sequence[Target | Path | str] | None = None,
) -> Target:
    """Compile an Inno Setup script into a setup program with ISCC.exe.

    Inno Setup builds a single ``.exe`` that installs files anywhere,
    including shared folders other programs read, such as plugin folders
    under Common Files, which an MSIX package can't reach. The script
    (``.iss``) describes the installer; this runs the compiler on it as a
    build step and tells it what to pack.

    ``sources`` are staged into one directory, which the script reads as
    ``{#StagingDir}``::

        [Files]
        Source: "{#StagingDir}\\*"; DestDir: "{app}"; Flags: recursesubdirs

    Each of ``defines`` is passed as ``/D<name>=<value>``, read in the
    script as ``{#<name>}``. Inno Setup reads other relative paths in the
    script (``SetupIconFile``, ``LicenseFile``, ``#include``) from the
    script's own directory, as usual.

    Args:
        project: Pcons project.
        env: Configured environment.
        name: The installer's name, for its staging directory and its
            default output.
        script: The ``.iss`` script, read from the build script's directory.
        sources: Files or directories to stage for the script to pack
            (Targets or paths). A directory source stages as a
            subdirectory. Without any, ``StagingDir`` is not defined.
        defines: Preprocessor values for the script. A ``str`` is passed
            as written. A ``Path`` (read from the build script's
            directory), ``Target`` or ``FileNode`` is a file the script
            reads, passed as an absolute path and tracked as an input, so
            changing it rebuilds the installer.
        output: The setup program, written like ``target=``. It must end
            in ``.exe``; this decides the script's ``OutputDir`` and
            ``OutputBaseFilename``. Defaults to ``<name>-setup.exe`` in
            the build directory.
        depends: Other inputs: files the script reads by itself
            (``#include`` files, icons, license text), so changing them
            rebuilds the installer, and Targets to build before staging,
            for a directory source that other targets populate.

    Returns:
        Target representing the setup program.

    Raises:
        ToolNotFoundError: If ISCC.exe is not found.
        ValueError: If ``output`` doesn't end in ``.exe``.
    """
    iscc = _find_iscc()
    if iscc is None:
        from pcons.contrib.installers._helpers import ToolNotFoundError

        raise ToolNotFoundError(
            "ISCC.exe",
            "Install Inno Setup: https://jrsoftware.org/isinfo.php "
            "(or winget install JRSoftware.InnoSetup)",
        )

    output = Path(output) if output is not None else Path(f"{name}-setup.exe")
    if output.suffix.lower() != ".exe":
        raise PconsValueError(
            f"create_inno_setup: output must be an .exe, not {output}"
        )

    depends = list(depends or ())
    define_args: list[str | Path | Target | FileNode] = []
    if sources:
        staging = staging_dir(env, "inno", name)
        stage_target = project.Install(staging, sources, no_prefix=True, env=env)
        stage_target.depends(*(d for d in depends if not isinstance(d, (Path, str))))
        depends.append(stage_target)
        define_args += ["--define-path", "StagingDir", staging]
    for key, value in (defines or {}).items():
        if isinstance(value, str):
            # Written as given: a "$" is the script's, not a pcons variable.
            define_args += ["--define", key, value.replace("$", "$$")]
        else:
            define_args += ["--define-path", key, value]
            if isinstance(value, Path):  # nodes are inputs already
                depends.append(value)

    return as_installer_step(
        env.Command(
            target=env.build_dir / output,
            source=[script],
            command=[
                sys.executable.replace("\\", "/"),
                "-m",
                "pcons.contrib.installers._helpers",
                "run_iscc",
                "--iscc",
                iscc,
                "--script",
                "$SOURCE",
                "--output",
                "$TARGET",
                *define_args,
            ],
            depends=depends,
        ),
        by="create_inno_setup",
    )
