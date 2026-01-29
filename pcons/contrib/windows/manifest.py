# SPDX-License-Identifier: MIT
"""Windows SxS manifest generation helpers.

This module provides functions for generating Windows manifests:
- create_app_manifest(): Generate application manifests with DPI, UAC, visual styles
- create_assembly_manifest(): Generate assembly manifests for private DLL assemblies

Manifests can be embedded in executables using pcons:
    app = project.Program("myapp", env)
    app.add_sources(["src/main.c", "app.manifest"])
    # Automatically passes /MANIFESTINPUT:app.manifest to linker

References:
- Application Manifests: https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests
- Assembly Manifests: https://learn.microsoft.com/en-us/windows/win32/sbscs/assembly-manifests
- /MANIFESTINPUT: https://learn.microsoft.com/en-us/cpp/build/reference/manifestinput-specify-manifest-input
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target

# XML namespaces used in Windows manifests
NS_ASM_V1 = "urn:schemas-microsoft-com:asm.v1"
NS_ASM_V3 = "urn:schemas-microsoft-com:asm.v3"
NS_COMPAT = "urn:schemas-microsoft-com:compatibility.v1"
NS_WINSETTINGS = "http://schemas.microsoft.com/SMI/2005/WindowsSettings"
NS_WINSETTINGS2 = "http://schemas.microsoft.com/SMI/2016/WindowsSettings"
NS_WINSETTINGS3 = "http://schemas.microsoft.com/SMI/2017/WindowsSettings"

# Windows version GUIDs for compatibility section
WINDOWS_VERSION_GUIDS = {
    "win10": "{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}",  # Windows 10 / 11
    "win81": "{1f676c76-80e1-4239-95bb-83d0f6d0da78}",  # Windows 8.1
    "win8": "{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}",  # Windows 8
    "win7": "{35138b9a-5d96-4fbd-8e2d-a2440225f93a}",  # Windows 7
    "vista": "{e2011457-1546-43c5-a5fe-008deee3d3f0}",  # Windows Vista
}


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    """Add indentation to XML element for pretty printing."""
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for i, child in enumerate(elem):
            _indent_xml(child, level + 1)
            if i == len(elem) - 1:
                if not child.tail or not child.tail.strip():
                    child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def _create_manifest_xml(
    *,
    dpi_aware: str | bool = False,
    visual_styles: bool = False,
    uac_level: str | None = None,
    supported_os: list[str] | None = None,
    assembly_deps: list[tuple[str, str]] | None = None,
    arch: str | None = None,
) -> str:
    """Create application manifest XML content.

    Args:
        dpi_aware: DPI awareness setting. Can be:
            - False: Not DPI aware (default, blurry on high-DPI)
            - True or "system": System DPI aware
            - "PerMonitor": Per-monitor DPI aware (Windows 8.1+)
            - "PerMonitorV2": Per-monitor V2 (Windows 10 1703+, recommended)
        visual_styles: Enable Windows visual styles (Common Controls v6).
        uac_level: UAC execution level:
            - "asInvoker": Run with user's current privileges (default)
            - "requireAdministrator": Require admin elevation
            - "highestAvailable": Use highest available privileges
        supported_os: List of supported Windows versions for compatibility.
            Options: "win10", "win81", "win8", "win7", "vista"
        assembly_deps: List of (assembly_name, version) tuples for dependencies.
        arch: Processor architecture for assembly identity.

    Returns:
        XML string for the manifest.
    """
    # Register namespaces to avoid ns0: prefixes
    ET.register_namespace("", NS_ASM_V1)
    ET.register_namespace("asmv3", NS_ASM_V3)
    ET.register_namespace("compat", NS_COMPAT)
    ET.register_namespace("ws", NS_WINSETTINGS)
    ET.register_namespace("ws2", NS_WINSETTINGS2)
    ET.register_namespace("ws3", NS_WINSETTINGS3)

    root = ET.Element("assembly", xmlns=NS_ASM_V1, manifestVersion="1.0")

    # Add assembly dependencies
    if assembly_deps:
        for dep_name, dep_version in assembly_deps:
            dep = ET.SubElement(root, "dependency")
            dep_asm = ET.SubElement(dep, "dependentAssembly")
            attribs: dict[str, str] = {
                "type": "win32",
                "name": dep_name,
                "version": dep_version,
            }
            if arch:
                attribs["processorArchitecture"] = arch
            ET.SubElement(dep_asm, "assemblyIdentity", attribs)

    # Add visual styles dependency (Common Controls v6)
    if visual_styles:
        dep = ET.SubElement(root, "dependency")
        dep_asm = ET.SubElement(dep, "dependentAssembly")
        ET.SubElement(
            dep_asm,
            "assemblyIdentity",
            type="win32",
            name="Microsoft.Windows.Common-Controls",
            version="6.0.0.0",
            processorArchitecture="*",
            publicKeyToken="6595b64144ccf1df",
            language="*",
        )

    # Add UAC settings
    if uac_level:
        trust_info = ET.SubElement(root, f"{{{NS_ASM_V3}}}trustInfo")
        security = ET.SubElement(trust_info, f"{{{NS_ASM_V3}}}security")
        requested = ET.SubElement(security, f"{{{NS_ASM_V3}}}requestedPrivileges")
        ET.SubElement(
            requested,
            f"{{{NS_ASM_V3}}}requestedExecutionLevel",
            level=uac_level,
            uiAccess="false",
        )

    # Add compatibility section
    if supported_os:
        compat = ET.SubElement(root, f"{{{NS_COMPAT}}}compatibility")
        app = ET.SubElement(compat, f"{{{NS_COMPAT}}}application")
        for os_name in supported_os:
            guid = WINDOWS_VERSION_GUIDS.get(os_name.lower())
            if guid:
                ET.SubElement(app, f"{{{NS_COMPAT}}}supportedOS", Id=guid)

    # Add DPI awareness settings
    if dpi_aware:
        app_settings = ET.SubElement(root, f"{{{NS_ASM_V3}}}application")
        win_settings = ET.SubElement(app_settings, f"{{{NS_ASM_V3}}}windowsSettings")

        # Normalize dpi_aware value
        if dpi_aware is True or dpi_aware == "system":
            dpi_value = "true"
            dpi_v2_value = None
        elif dpi_aware == "PerMonitor":
            dpi_value = "true/pm"
            dpi_v2_value = None
        elif dpi_aware == "PerMonitorV2":
            dpi_value = "true/pm"
            dpi_v2_value = "PerMonitorV2"
        else:
            dpi_value = str(dpi_aware)
            dpi_v2_value = None

        # dpiAware element (Windows Vista+)
        dpi_elem = ET.SubElement(win_settings, f"{{{NS_WINSETTINGS}}}dpiAware")
        dpi_elem.text = dpi_value

        # dpiAwareness element (Windows 10 1607+) for PerMonitorV2
        if dpi_v2_value:
            dpi2_elem = ET.SubElement(
                win_settings, f"{{{NS_WINSETTINGS3}}}dpiAwareness"
            )
            dpi2_elem.text = dpi_v2_value

    _indent_xml(root)

    # Generate XML with declaration
    xml_decl = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    return xml_decl + ET.tostring(root, encoding="unicode")


def _create_assembly_manifest_xml(
    *,
    name: str,
    version: str,
    dlls: list[str],
    arch: str = "x64",
) -> str:
    """Create assembly manifest XML content.

    Args:
        name: Assembly name (e.g., "MyLib.Assembly").
        version: Version string (e.g., "1.0.0.0").
        dlls: List of DLL filenames in this assembly.
        arch: Processor architecture (x86, amd64, arm64).

    Returns:
        XML string for the manifest.
    """
    ET.register_namespace("", NS_ASM_V1)

    # Map common arch names to manifest values
    arch_map = {
        "x64": "amd64",
        "x86_64": "amd64",
        "amd64": "amd64",
        "x86": "x86",
        "i386": "x86",
        "i686": "x86",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    proc_arch = arch_map.get(arch.lower(), arch)

    root = ET.Element("assembly", xmlns=NS_ASM_V1, manifestVersion="1.0")

    # Assembly identity
    ET.SubElement(
        root,
        "assemblyIdentity",
        type="win32",
        name=name,
        version=version,
        processorArchitecture=proc_arch,
    )

    # File entries for each DLL
    for dll in dlls:
        ET.SubElement(root, "file", name=dll)

    _indent_xml(root)

    xml_decl = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    return xml_decl + ET.tostring(root, encoding="unicode")


def create_app_manifest(
    project: Project,  # noqa: ARG001  # Reserved for future use/API consistency
    env: Environment,
    *,
    output: str | Path,
    dpi_aware: str | bool = False,
    visual_styles: bool = False,
    uac_level: str | None = None,
    supported_os: list[str] | None = None,
    assembly_deps: list[tuple[str, str]] | None = None,
) -> Target:
    """Generate a Windows application manifest file.

    Creates a manifest file that can be embedded in an executable to configure
    Windows application settings like DPI awareness, UAC elevation level,
    visual styles, and assembly dependencies.

    Usage:
        app_manifest = manifest.create_app_manifest(
            project, env,
            output="app.manifest",
            dpi_aware="PerMonitorV2",
            visual_styles=True,
            uac_level="asInvoker",
        )

        app = project.Program("myapp", env)
        app.add_sources(["src/main.c", app_manifest])

    Args:
        project: Pcons project.
        env: Configured environment.
        output: Output manifest file path (relative to build dir).
        dpi_aware: DPI awareness setting:
            - False: Not DPI aware (default)
            - True or "system": System DPI aware
            - "PerMonitor": Per-monitor DPI aware (Windows 8.1+)
            - "PerMonitorV2": Per-monitor V2 (Windows 10 1703+, recommended)
        visual_styles: Enable Windows visual styles (Common Controls v6).
            Required for modern-looking controls.
        uac_level: UAC execution level:
            - "asInvoker": Run with user's current privileges (default)
            - "requireAdministrator": Require admin elevation
            - "highestAvailable": Use highest available privileges
        supported_os: List of supported Windows versions. Declaring support
            enables Windows to provide full functionality for your app.
            Options: "win10", "win81", "win8", "win7", "vista"
        assembly_deps: List of (assembly_name, version) tuples for private
            assembly dependencies. Used with create_assembly_manifest().

    Returns:
        Target representing the generated manifest file. Can be added directly
        to a Program's sources and will be passed to linker via /MANIFESTINPUT.
    """
    output_path = Path(output)

    # Detect architecture from environment
    arch = getattr(env, "target_arch", None)

    # Generate manifest content
    xml_content = _create_manifest_xml(
        dpi_aware=dpi_aware,
        visual_styles=visual_styles,
        uac_level=uac_level,
        supported_os=supported_os,
        assembly_deps=assembly_deps,
        arch=arch,
    )

    # Use Command to generate the manifest file
    # We use Python to write the file content
    import sys

    python_cmd = sys.executable.replace("\\", "/")

    # Create command that writes the manifest
    # We pass the content via a heredoc-style approach using -c
    target = env.Command(
        target=output_path,
        source=None,
        command=[
            python_cmd,
            "-c",
            f"import pathlib; pathlib.Path({str(output_path)!r}).write_text({xml_content!r}, encoding='utf-8')",
        ],
        name=f"manifest_{output_path.stem}",
    )

    return target


def create_assembly_manifest(
    project: Project,  # noqa: ARG001  # Reserved for future use/API consistency
    env: Environment,
    *,
    name: str,
    version: str,
    dlls: list[Target | str],
    output: str | Path | None = None,
    arch: str | None = None,
) -> Target:
    """Generate a Windows assembly manifest for a collection of DLLs.

    Creates a manifest file that defines a named assembly containing one or
    more DLLs. Applications can declare dependencies on this assembly in their
    app manifest, and Windows will locate the DLLs through the assembly system.

    For private assemblies, place the manifest file alongside the DLLs in one of:
    - <appdir>/<assemblyname>.manifest
    - <appdir>/<assemblyname>/<assemblyname>.manifest

    Usage:
        # Create DLLs
        mylib = project.SharedLibrary("MyLib", env, sources=["lib.c"])
        helper = project.SharedLibrary("MyHelper", env, sources=["helper.c"])

        # Generate assembly manifest
        assembly = manifest.create_assembly_manifest(
            project, env,
            name="MyApp.Libraries",
            version="1.0.0.0",
            dlls=[mylib, helper],
        )

        # Application declares dependency on the assembly
        app_manifest = manifest.create_app_manifest(
            project, env,
            output="app.manifest",
            assembly_deps=[("MyApp.Libraries", "1.0.0.0")],
        )

    Args:
        project: Pcons project.
        env: Configured environment.
        name: Assembly name (e.g., "MyApp.Libraries"). This name is used in
            dependent application manifests and for locating the manifest file.
        version: Version string in X.Y.Z.W format (e.g., "1.0.0.0").
        dlls: List of DLLs in this assembly. Can be Target objects (SharedLibrary)
            or filename strings.
        output: Output manifest file path. Defaults to "<name>.manifest".
        arch: Processor architecture. If not specified, detected from environment.
            Options: "x64", "x86", "arm64"

    Returns:
        Target representing the generated manifest file.
    """
    # Default output path
    if output is None:
        output_path = Path(f"{name}.manifest")
    else:
        output_path = Path(output)

    # Detect architecture from environment if not specified
    if arch is None:
        arch = getattr(env, "target_arch", "x64")
        # If still no arch, default to x64
        if arch is None:
            arch = "x64"

    # Convert Target objects to DLL filenames
    dll_names: list[str] = []
    for dll in dlls:
        if isinstance(dll, str):
            dll_names.append(dll)
        elif hasattr(dll, "output_name") and dll.output_name:
            # Target with output_name
            dll_names.append(str(dll.output_name))
        elif hasattr(dll, "name"):
            # Target without output_name - use name + .dll
            dll_name = str(dll.name)
            if not dll_name.lower().endswith(".dll"):
                dll_name = f"{dll_name}.dll"
            dll_names.append(dll_name)

    # Generate manifest content
    xml_content = _create_assembly_manifest_xml(
        name=name,
        version=version,
        dlls=dll_names,
        arch=arch,
    )

    # Use Command to generate the manifest file
    import sys

    python_cmd = sys.executable.replace("\\", "/")

    target = env.Command(
        target=output_path,
        source=None,
        command=[
            python_cmd,
            "-c",
            f"import pathlib; pathlib.Path({str(output_path)!r}).write_text({xml_content!r}, encoding='utf-8')",
        ],
        name=f"assembly_{name.replace('.', '_')}",
    )

    return target
