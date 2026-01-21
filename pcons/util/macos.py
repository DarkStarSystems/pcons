# SPDX-License-Identifier: MIT
"""macOS-specific utilities for pcons.

These utilities help with common macOS build tasks like:
- Managing dynamic library paths for bundles
- Creating universal binaries from architecture-specific builds
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


def get_dylib_install_name(path: Path | str) -> str:
    """Get a dylib's install name.

    The install name is the path recorded in binaries that link to this
    library. It's often different from the actual filename (e.g., a
    symlink name like "libfoo.1.dylib" vs the real file "libfoo.1.2.3.dylib").

    Args:
        path: Path to the dylib file.

    Returns:
        The basename of the install name (e.g., "libopencv_core.412.dylib").

    Raises:
        subprocess.CalledProcessError: If otool fails.
        ValueError: If the dylib has no install name.

    Example:
        >>> install_name = get_dylib_install_name("/opt/homebrew/lib/libfoo.1.2.3.dylib")
        >>> # Returns "libfoo.1.dylib" (the install name, not the filename)
        >>> plugin.post_build(f"install_name_tool -change /opt/homebrew/lib/{install_name} @rpath/{install_name} $out")
        >>> project.InstallAs(bundle_dir / install_name, dylib_path)
    """
    path = Path(path)
    output = subprocess.check_output(
        ["otool", "-D", str(path)],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()

    # Output format is:
    # /path/to/lib.dylib:
    # /install/path/lib.dylib
    lines = output.split("\n")
    if len(lines) < 2:
        raise ValueError(f"No install name found for {path}")

    install_path = lines[-1].strip()
    return Path(install_path).name


def fix_dylib_references(
    target: Any,
    dylibs: list[Path | str],
    lib_dir: Path | str,
    *,
    rpath_prefix: str = "@rpath",
) -> list[str]:
    """Add post_build commands to fix dylib references in a target.

    This is a convenience function that generates install_name_tool commands
    to change absolute dylib paths to rpath-relative paths.

    Args:
        target: The Target to add post_build commands to.
        dylibs: List of dylib paths to fix references for.
        lib_dir: The directory where dylibs are installed (the absolute path
                 that will be replaced).
        rpath_prefix: The rpath prefix to use (default: "@rpath").

    Returns:
        List of install names (basenames) for the dylibs, useful for
        InstallAs operations.

    Example:
        >>> install_names = fix_dylib_references(
        ...     ofx_plugin,
        ...     opencv_dylibs,
        ...     "/opt/homebrew/opt/opencv/lib",
        ... )
        >>> for dylib, name in zip(opencv_dylibs, install_names):
        ...     project.InstallAs(bundle_dir / name, dylib)
    """
    lib_dir = Path(lib_dir)
    install_names = []

    for dylib in dylibs:
        dylib = Path(dylib)
        install_name = get_dylib_install_name(dylib)
        install_names.append(install_name)

        target.post_build(
            f"install_name_tool -change {lib_dir}/{install_name} "
            f"{rpath_prefix}/{install_name} $out"
        )

    return install_names


def create_universal_binary(
    project: Project,
    name: str,
    inputs: list[Target | FileNode | Path | str],
    output: Path | str,
) -> Target:
    """Create a macOS universal binary by combining architecture-specific binaries.

    Uses `lipo -create` to combine multiple architecture-specific binaries
    (e.g., arm64 and x86_64) into a single universal binary.

    This is typically used after building the same target for multiple
    architectures using set_target_arch().

    Args:
        project: The pcons Project instance.
        name: A unique name for this universal binary target.
        inputs: List of architecture-specific binaries to combine.
                Can be Target objects (uses their output files), FileNode objects,
                or Path/str paths to files.
        output: Path for the output universal binary.

    Returns:
        Target object representing the universal binary.

    Example:
        from pcons import Project, find_c_toolchain
        from pcons.util.macos import create_universal_binary

        project = Project("mylib")
        toolchain = find_c_toolchain()

        # Build for each architecture
        env_arm64 = project.Environment(toolchain=toolchain)
        env_arm64.set_target_arch("arm64")
        env_arm64.build_dir = Path("build/arm64")

        env_x86_64 = project.Environment(toolchain=toolchain)
        env_x86_64.set_target_arch("x86_64")
        env_x86_64.build_dir = Path("build/x86_64")

        lib_arm64 = project.StaticLibrary("mylib", env_arm64, sources=["lib.c"])
        lib_x86_64 = project.StaticLibrary("mylib", env_x86_64, sources=["lib.c"])

        # Combine into universal binary
        lib_universal = create_universal_binary(
            project, "mylib_universal",
            inputs=[lib_arm64, lib_x86_64],
            output="build/universal/libmylib.a"
        )
    """
    from pcons.core.node import FileNode
    from pcons.core.target import Target

    output_path = Path(output)

    # Resolve inputs to file paths
    input_nodes: list[FileNode] = []
    for inp in inputs:
        if isinstance(inp, Target):
            # Get the target's output nodes
            if inp.output_nodes:
                for node in inp.output_nodes:
                    if isinstance(node, FileNode):
                        input_nodes.append(node)
            elif inp.nodes:
                # Fallback to nodes for unresolved targets
                for node in inp.nodes:
                    if isinstance(node, FileNode):
                        input_nodes.append(node)
        elif isinstance(inp, FileNode):
            input_nodes.append(inp)
        elif isinstance(inp, (Path, str)):
            input_nodes.append(FileNode(Path(inp)))

    if not input_nodes:
        raise ValueError("create_universal_binary requires at least one input")

    # Get any environment from the project to use for Command()
    # We need an environment to call Command(), but lipo doesn't need
    # toolchain-specific settings
    if project.environments:
        env = project.environments[0]
    else:
        # Create a minimal environment if none exists
        from pcons.core.environment import Environment

        env = Environment()
        env._project = project

    # Create the lipo command
    # The command uses $SOURCES and $TARGET (generator-agnostic variables)
    # Convert FileNodes to paths for the source argument
    source_paths: list[Path | str] = [node.path for node in input_nodes]

    lipo_target = env.Command(
        target=output_path,
        source=source_paths,
        command="lipo -create -output $TARGET $SOURCES",
        name=name,
    )

    # Mark the build info with tool="lipo" for the ninja generator
    # This is used after resolve() populates output_nodes
    build_info = lipo_target._build_info if lipo_target._build_info is not None else {}
    build_info["tool"] = "lipo"
    lipo_target._build_info = build_info

    return lipo_target
