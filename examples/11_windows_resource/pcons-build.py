"""Build script demonstrating Windows resource file compilation.

This example shows how to compile Windows resource files (.rc) along with
C source files using the MSVC toolchain. The resource file contains version
information that gets embedded into the executable.

Windows-only: requires MSVC toolchain.
"""

from pcons import Project

# Create project
project = Project("resource_example")

# Directories
src_dir = project.root_dir / "src"

# Find C toolchain - prefer MSVC or clang-cl on Windows for resource file support
env = project.Environment(toolchain=["msvc", "clang-cl", "gcc", "llvm"])

# Create program with C source and Windows resource file
app = project.Program("myapp", env)
app.add_sources([src_dir / "main.c", src_dir / "app.rc"])

# Set as default target
project.Default(app)
