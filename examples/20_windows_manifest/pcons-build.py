"""Windows SxS manifests: an app manifest and a private assembly.

Two separate things, both built here:

1. An **app manifest**, embedded into myapp.exe by the linker. This is where
   DPI awareness, visual styles and UAC level live.
2. A **private assembly** -- MyLib.dll in a subdirectory of its own, with an
   assembly manifest beside it, which myapp.exe declares a dependency on.

The second is the one worth looking at closely. MyLib.dll is deliberately
*not* next to myapp.exe, so the ordinary DLL search cannot find it. The only
thing that resolves it is the activation context Windows builds from the two
manifests. If either manifest is wrong the process does not start at all --
which is what makes running myapp.exe a real test rather than a smoke test.

Windows-only: requires MSVC or clang-cl.
"""

import platform

from pcons import Project

project = Project("manifest_example")

src_dir = project.root_dir / "src"

env = project.Environment(toolchain=["msvc", "clang-cl", "gcc", "llvm"])

is_windows_toolchain = env.toolchain.name in ("msvc", "clang-cl")

# The assembly's identity. The name is also the directory it lives in, which
# is one of the two layouts Windows probes for a private assembly.
ASSEMBLY_NAME = "ManifestExample.MyLib"
ASSEMBLY_VERSION = "1.0.0.0"

mylib = project.SharedLibrary("MyLib", env)
mylib.add_sources([src_dir / "mylib.c"])

app = project.Program("myapp", env)
app.add_sources([src_dir / "main.c"])
app.link(mylib)

if is_windows_toolchain:
    from pcons.contrib.windows import manifest

    # Both manifests must name the architecture identically -- Windows matches
    # the identity literally -- so it is derived once and passed to both.
    arch = platform.machine()

    # Placing the DLL here is what takes it out of reach of the ordinary
    # search, leaving the activation context as the only way to find it.
    mylib.output_prefix = f"{ASSEMBLY_NAME}/"

    assembly = manifest.create_assembly_manifest(
        project,
        env,
        name=ASSEMBLY_NAME,
        version=ASSEMBLY_VERSION,
        dlls=[mylib],
        output=f"{ASSEMBLY_NAME}/{ASSEMBLY_NAME}.manifest",
        arch=arch,
    )

    app_manifest = manifest.create_app_manifest(
        project,
        env,
        output="app.manifest",
        dpi_aware="PerMonitorV2",
        visual_styles=True,
        uac_level="asInvoker",
        supported_os=["win10", "win81", "win7"],
        assembly_deps=[(ASSEMBLY_NAME, ASSEMBLY_VERSION)],
        arch=arch,
    )

    # Added as a source, the manifest reaches the linker as /MANIFESTINPUT and
    # is embedded as a resource. No separate mt.exe step is involved.
    app.add_sources([app_manifest])
    app.depends(assembly)

    project.Default(app, mylib, assembly)
else:
    project.Default(app, mylib)
