"""Build script demonstrating Windows SxS manifest support.

This example shows how to:
1. Add a .manifest file as a source to embed it via /MANIFESTINPUT
2. Generate app manifests with DPI awareness and visual styles
3. Generate assembly manifests for private DLL assemblies

Windows-only: requires MSVC or clang-cl toolchain.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Create project
project = Project("manifest_example", build_dir=build_dir)

# Find C toolchain - prefer MSVC or clang-cl on Windows for manifest support
toolchain = find_c_toolchain(prefer=["msvc", "clang-cl", "gcc", "llvm"])
env = project.Environment(toolchain=toolchain)

# Example 1: Create a DLL that will be part of an assembly
mylib = project.SharedLibrary("MyLib", env)
mylib.add_sources([src_dir / "mylib.c"])

# Check if we're on Windows with manifest support
is_windows_toolchain = toolchain.name in ("msvc", "clang-cl")

if is_windows_toolchain:
    from pcons.contrib.windows import manifest

    # Example 2: Generate assembly manifest for the DLL
    # This manifest file declares the DLL as a named assembly
    assembly = manifest.create_assembly_manifest(
        project,
        env,
        name="ManifestExample.MyLib",
        version="1.0.0.0",
        dlls=[mylib],
    )

    # Example 3: Generate app manifest with common settings
    # - DPI awareness for crisp rendering on high-DPI displays
    # - Visual styles for modern Windows controls
    # - Supported OS declaration for full Windows 10 functionality
    app_manifest = manifest.create_app_manifest(
        project,
        env,
        output="app.manifest",
        dpi_aware="PerMonitorV2",
        visual_styles=True,
        supported_os=["win10", "win81", "win7"],
    )

    # Example 4: Create program with embedded manifest
    # The manifest file is automatically passed to the linker via /MANIFESTINPUT
    app = project.Program("myapp", env)
    app.add_sources([src_dir / "main.c", app_manifest])

    # Set as default targets
    project.Default(app, mylib, assembly)
else:
    # On non-Windows, just build a simple app without manifest
    app = project.Program("myapp", env)
    app.add_sources([src_dir / "main.c"])
    project.Default(app, mylib)

# Resolve dependencies
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project)

print(f"Generated {build_dir} using {toolchain.name} toolchain")
if is_windows_toolchain:
    print("  - MyLib.dll with assembly manifest")
    print("  - myapp.exe with embedded app manifest (DPI aware, visual styles)")
