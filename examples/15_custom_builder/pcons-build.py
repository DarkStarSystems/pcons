#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Example: Creating a custom builder.

This example demonstrates how to extend pcons with custom builders using
the @builder decorator. Custom builders are first-class citizens - they
work exactly like built-in builders (Program, Install, etc.).

The example creates a "GenerateVersion" builder that generates a C header
file with version information.
"""

from pathlib import Path

from pcons import Generator, Project, find_c_toolchain
from pcons.core.builder_registry import builder
from pcons.core.node import FileNode
from pcons.core.target import Target, TargetType
from pcons.util.source_location import get_caller_location

# =============================================================================
# Custom Builder Definition
# =============================================================================


def _create_version_header_content(app_name: str, version: str) -> str:
    """Generate the content of the version header."""
    parts = version.split(".")
    major = parts[0] if len(parts) > 0 else "0"
    minor = parts[1] if len(parts) > 1 else "0"
    patch = parts[2] if len(parts) > 2 else "0"

    return f'''/* Auto-generated version header */
#ifndef VERSION_H
#define VERSION_H

#define APP_NAME "{app_name}"
#define VERSION_STRING "{version}"
#define VERSION_MAJOR {major}
#define VERSION_MINOR {minor}
#define VERSION_PATCH {patch}

#endif /* VERSION_H */
'''


@builder(
    "GenerateVersion",
    target_type=TargetType.COMMAND,
    description="Generate a version header file",
)
class GenerateVersionBuilder:
    """Generate a C header file with version information.

    This custom builder creates a version.h file containing version macros
    that can be included in C/C++ code.

    Usage:
        project.GenerateVersion("version.h", version="1.2.3", app_name="MyApp")
    """

    @staticmethod
    def create_target(
        project: Project,
        output: str | Path,
        *,
        version: str = "1.0.0",
        app_name: str = "App",
        name: str | None = None,
    ) -> Target:
        """Create a GenerateVersion target.

        Args:
            project: The project to add the target to.
            output: Output header file path (relative to build_dir).
            version: Version string (e.g., "1.2.3").
            app_name: Application name for the header.
            name: Optional target name.

        Returns:
            A Target representing the version header generation.
        """
        output_path = project.path_resolver.normalize_target_path(output)
        target_name = name or f"version_{output_path.stem}"

        target = Target(
            target_name,
            target_type=TargetType.COMMAND,
            defined_at=get_caller_location(),
        )
        target._project = project
        target._builder_name = "GenerateVersion"

        # Create the output node immediately (not using pending sources)
        # This allows other targets to depend on the output node
        output_node = FileNode(output_path, defined_at=get_caller_location())

        # Generate the header content and build the command.
        # Use a triple-quoted Python string to avoid escaping issues.
        # The content is base64-encoded to avoid any shell/Python quoting problems.
        import base64
        import sys

        header_content = _create_version_header_content(app_name, version)
        encoded = base64.b64encode(header_content.encode()).decode()

        # Use sys.executable to get the Python interpreter path (works on all platforms)
        python_cmd = sys.executable.replace("\\", "/")  # Use forward slashes for ninja

        output_node._build_info = {
            "tool": "generate_version",
            "command": f"\"{python_cmd}\" -c \"import base64; open(__import__('sys').argv[1], 'w').write(base64.b64decode('{encoded}').decode())\" $out",
        }

        # Register the node and add to target
        project._nodes[output_path] = output_node
        target.output_nodes.append(output_node)
        target.nodes.append(output_node)

        project.add_target(target)
        return target


# =============================================================================
# Build Script
# =============================================================================

project = Project("custom_builder_example")
build_dir = project.build_dir

# Find a C toolchain
toolchain = find_c_toolchain()
env = project.Environment(toolchain=toolchain)

# Use our custom builder to generate a version header
# This works exactly like built-in builders!
version_header = project.GenerateVersion(
    "version.h",
    version="2.1.0",
    app_name="CustomBuilderDemo",
)

# Make sure the header is in the include path (before creating the program)
env.cc.includes.append(build_dir)

# Build a program that uses the generated header
app = project.Program("demo", env, sources=["main.c"])

# The version header must be generated before compiling main.c.
# We add an explicit source dependency on the generated header node.
# This creates a ninja dependency edge: main.o depends on version.h
for src_node in app.sources:
    src_node.depends(version_header.output_nodes)

project.Default(app)

# Generate build files
Generator().generate(project)

print(f"Generated build files in {build_dir}")
print("Run 'ninja -C build' to build, then './build/demo' to run")
