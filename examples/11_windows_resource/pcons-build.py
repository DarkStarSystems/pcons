"""Build script demonstrating Windows resource file compilation.

This example shows how to compile Windows resource files (.rc) along with
C source files using the MSVC toolchain. The resource file contains version
information that gets embedded into the executable.

Windows-only: requires MSVC toolchain.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Create project
project = Project("resource_example", build_dir=build_dir)

# Find C toolchain - prefer MSVC or clang-cl on Windows for resource file support
toolchain = find_c_toolchain(prefer=["msvc", "clang-cl", "gcc", "llvm"])
env = project.Environment(toolchain=toolchain)

# Create program with C source and Windows resource file
app = project.Program("myapp", env)
app.add_sources([src_dir / "main.c", src_dir / "app.rc"])

# Set as default target
project.Default(app)

# Resolve dependencies
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project)

print(f"Generated {build_dir} using {toolchain.name} toolchain")
