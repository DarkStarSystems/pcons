# SPDX-License-Identifier: MIT
"""Target resolution system for target-centric builds.

The Resolver is responsible for:
1. Computing effective requirements for each target
2. Creating object nodes for source files with the effective flags
3. Creating output nodes (libraries, programs) with proper dependencies
4. Object file caching (same source + same flags = shared object)

The resolver is designed to be tool-agnostic: it delegates source handling
to the toolchain rather than having hardcoded knowledge about file types.

The resolution logic is organized into focused factory classes:
- ObjectNodeFactory: Creates and caches object nodes for source files
- OutputNodeFactory: Creates library and program output nodes
- InstallNodeFactory: Creates install/copy nodes for install targets
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.build_context import CompileLinkContext
from pcons.core.graph import topological_sort_targets
from pcons.core.node import FileNode
from pcons.core.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


# Legacy mapping from source suffix to (tool_name, language)
# DEPRECATED: Use toolchain.get_source_handler() instead.
# This is kept for backwards compatibility when no toolchain is available.
SOURCE_SUFFIX_MAP: dict[str, tuple[str, str]] = {
    ".c": ("cc", "c"),
    ".cpp": ("cxx", "cxx"),
    ".cxx": ("cxx", "cxx"),
    ".cc": ("cxx", "cxx"),
    ".c++": ("cxx", "cxx"),
    ".C": ("cxx", "cxx"),
    ".m": ("cc", "objc"),
    ".mm": ("cxx", "objcxx"),
}


class ObjectNodeFactory:
    """Factory for creating and caching object file nodes.

    Handles object node creation with caching to avoid duplicate compilations
    when the same source is compiled with the same effective requirements.

    Attributes:
        project: The project being resolved.
        _object_cache: Cache mapping (source_path, effective_hash) to object node.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory.

        Args:
            project: The project to resolve.
        """
        self.project = project
        self._object_cache: dict[tuple[Path, tuple], FileNode] = {}

    def get_object_path(self, target: Target, source: Path, env: Environment) -> Path:
        """Generate target-specific output path for an object file.

        Format: build/obj.<target_name>/<source_stem>.<suffix>

        The "obj." prefix avoids naming conflicts between the object directory
        and the final output file (e.g., program named "hello" vs directory
        containing object files).

        The object suffix is obtained from the source handler if available
        (e.g., ".res" for resource files), otherwise from the toolchain's
        default object suffix.

        Args:
            target: The target owning this object.
            source: Source file path.
            env: Environment containing the toolchain.

        Returns:
            Path for the object file.
        """
        build_dir = self.project.build_dir
        # Use "obj.<target_name>" as subdirectory to avoid conflicts with outputs
        obj_dir = build_dir / f"obj.{target.name}"

        # Try to get suffix from source handler first (for special file types like .rc)
        handler = self.get_source_handler(source, env)
        if handler:
            suffix = handler.object_suffix
        else:
            # Fallback to toolchain's default object suffix
            toolchain = env._toolchain
            suffix = toolchain.get_object_suffix() if toolchain else ".o"

        obj_name = source.stem + suffix
        return obj_dir / obj_name

    def get_source_handler(
        self, source: Path, env: Environment
    ) -> SourceHandler | None:
        """Get source handler from any of the environment's toolchains.

        This is the tool-agnostic way to determine how to compile a source.
        Checks all toolchains in order (primary first, then additional).
        Falls back to the legacy SOURCE_SUFFIX_MAP if no toolchain handles it.

        Args:
            source: Source file path.
            env: Environment containing the toolchain(s).

        Returns:
            SourceHandler if any toolchain can handle this source, else None.
        """
        # Check all toolchains in order (primary first, then additional)
        for toolchain in env.toolchains:
            handler = toolchain.get_source_handler(source.suffix)
            if handler is not None:
                if env.has_tool(handler.tool_name):
                    result: SourceHandler = handler
                    return result
                else:
                    logger.warning(
                        "Tool '%s' required for '%s' files is not available in the "
                        "environment. Configure the toolchain or add the tool manually.",
                        handler.tool_name,
                        source.suffix,
                    )
        return None

    def get_auxiliary_input_handler(
        self, source: Path, env: Environment
    ) -> AuxiliaryInputHandler | None:
        """Get auxiliary input handler from any of the environment's toolchains.

        Auxiliary input files are passed directly to a downstream tool with
        specific flags rather than being compiled to object files.

        Args:
            source: Source file path.
            env: Environment containing the toolchain(s).

        Returns:
            AuxiliaryInputHandler if any toolchain handles this as an auxiliary
            input, else None.
        """
        # Check all toolchains in order (primary first, then additional)
        for toolchain in env.toolchains:
            handler = toolchain.get_auxiliary_input_handler(source.suffix)
            if handler is not None:
                return handler
        return None

    def get_tool_for_source(
        self, source: Path, env: Environment
    ) -> tuple[str, str] | None:
        """Return (tool_name, language) based on source suffix.

        DEPRECATED: Use get_source_handler() for full handler info.
        This method is kept for backwards compatibility.

        Args:
            source: Source file path.
            env: Environment to check for tool availability.

        Returns:
            Tuple of (tool_name, language) or None if not recognized.
        """
        # Try toolchain first (tool-agnostic approach)
        handler = self.get_source_handler(source, env)
        if handler:
            return (handler.tool_name, handler.language)

        # Fallback to legacy hardcoded map
        suffix = source.suffix.lower()
        result = SOURCE_SUFFIX_MAP.get(suffix)
        if result:
            tool_name, language = result
            if env.has_tool(tool_name):
                logger.warning(
                    "Using deprecated SOURCE_SUFFIX_MAP fallback for '%s' files. "
                    "Consider configuring a toolchain that handles this suffix.",
                    suffix,
                )
                return result
        return None

    def create_object_node(
        self,
        target: Target,
        source: FileNode,
        effective: EffectiveRequirements,
        env: Environment,
    ) -> FileNode | None:
        """Create object file node with effective requirements in build_info.

        Implements object caching: if the same source is compiled with the
        same effective requirements, the same object node is reused.

        Args:
            target: The target this object belongs to.
            source: Source file node.
            effective: Computed effective requirements.
            env: Build environment.

        Returns:
            FileNode for the object file, or None if source type not recognized.
        """
        # Try to get a source handler from the toolchain (tool-agnostic)
        handler = self.get_source_handler(source.path, env)

        # Fallback to legacy approach if no handler
        if handler is None:
            tool_info = self.get_tool_for_source(source.path, env)
            if tool_info is None:
                return None
            tool_name, language = tool_info
            depfile: str | None = "$out.d"
            deps_style: str | None = "gcc"
            command_var: str = "objcmd"
        else:
            tool_name = handler.tool_name
            language = handler.language
            depfile = handler.depfile
            deps_style = handler.deps_style
            command_var = handler.command_var

        # Generate cache key: (source path, effective requirements hash)
        # Use resolved (absolute) path to avoid duplicate objects for same file
        effective_hash = effective.as_hashable_tuple()
        cache_key = (source.path.resolve(), effective_hash)

        # Check cache
        if cache_key in self._object_cache:
            return self._object_cache[cache_key]

        # Create object node
        obj_path = self.get_object_path(target, source.path, env)
        obj_node = FileNode(obj_path, defined_at=get_caller_location())
        obj_node.depends([source])

        # Create context object from effective requirements
        context = CompileLinkContext.from_effective_requirements(effective)

        # Store build info for the generator
        # Use handler's depfile/deps_style (from toolchain, not hardcoded)
        obj_node._build_info = {
            "tool": tool_name,
            "command_var": command_var,
            "language": language,
            "sources": [source],
            "depfile": depfile,
            "deps_style": deps_style,
            "context": context,
        }

        # Cache the object node
        self._object_cache[cache_key] = obj_node

        # Register with environment
        env.register_node(obj_node)

        return obj_node


class OutputNodeFactory:
    """Factory for creating library and program output nodes.

    Handles creation of static libraries, shared libraries, and programs
    with proper dependencies and link flags.

    Attributes:
        project: The project being resolved.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory.

        Args:
            project: The project to resolve.
        """
        self.project = project

    def create_static_library_output(self, target: Target, env: Environment) -> None:
        """Create static library output node.

        Args:
            target: The target to create output for.
            env: Build environment.
        """
        if not target.object_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir

        # Check for custom output name first
        if target.output_name:
            lib_name = target.output_name
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_static_library_name(target.name)
        else:
            lib_name = f"lib{target.name}.a"  # Fallback

        lib_path = build_dir / lib_name

        lib_node = FileNode(lib_path, defined_at=get_caller_location())
        lib_node.depends(target.object_nodes)

        # Compute effective link requirements
        effective_link = compute_effective_requirements(
            target, env, for_compilation=False
        )

        # Create context object from effective requirements
        context = CompileLinkContext.from_effective_requirements(effective_link)

        # Get archiver tool name from toolchain (e.g., "ar" for GCC, "lib" for MSVC)
        archiver_tool = "ar"  # default
        if toolchain := env._toolchain:
            archiver_tool = toolchain.get_archiver_tool_name()

        lib_node._build_info = {
            "tool": archiver_tool,
            "command_var": "libcmd",
            "sources": target.object_nodes,
            "context": context,
        }

        target.output_nodes.append(lib_node)
        target.nodes.append(lib_node)
        env.register_node(lib_node)

    def create_shared_library_output(self, target: Target, env: Environment) -> None:
        """Create shared library output node.

        Args:
            target: The target to create output for.
            env: Build environment.
        """
        if not target.object_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir

        # Check for custom output name first
        if target.output_name:
            lib_name = target.output_name
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_shared_library_name(target.name)
        else:
            # Fallback to platform-specific naming
            import sys

            if sys.platform == "darwin":
                lib_name = f"lib{target.name}.dylib"
            elif sys.platform == "win32":
                lib_name = f"{target.name}.dll"
            else:
                lib_name = f"lib{target.name}.so"

        lib_path = build_dir / lib_name

        lib_node = FileNode(lib_path, defined_at=get_caller_location())
        lib_node.depends(target.object_nodes)

        # Add dependency output nodes from linked targets
        dep_libs = self._collect_dependency_outputs(target)
        if dep_libs:
            lib_node.depends(dep_libs)

        # Add auxiliary input files as dependencies
        auxiliary_inputs = getattr(target, "_auxiliary_inputs", [])
        if auxiliary_inputs:
            linker_input_nodes = [node for node, _ in auxiliary_inputs]
            lib_node.depends(linker_input_nodes)

        # Compute effective link requirements
        effective_link = compute_effective_requirements(
            target, env, for_compilation=False
        )

        # Add auxiliary input flags to link flags
        link_flags = list(effective_link.link_flags)
        for _, flag in auxiliary_inputs:
            link_flags.append(flag)

        # Create context object from effective requirements
        # We need to update link_flags in effective_link before creating context
        effective_link.link_flags = link_flags
        context = CompileLinkContext.from_effective_requirements(effective_link)

        # Determine linker language - we use 'link' tool for linking
        # but track the language for description purposes
        link_language = "cxx" if "cxx" in target.get_all_languages() else "c"

        lib_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": link_language,
            "sources": target.object_nodes,
            "context": context,
        }

        target.output_nodes.append(lib_node)
        target.nodes.append(lib_node)
        env.register_node(lib_node)

    def create_program_output(self, target: Target, env: Environment) -> None:
        """Create program output node.

        Args:
            target: The target to create output for.
            env: Build environment.
        """
        if not target.object_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir

        # Check for custom output name first
        if target.output_name:
            prog_name = target.output_name
        elif toolchain := env._toolchain:
            prog_name = toolchain.get_program_name(target.name)
        else:
            # Fallback to platform-specific naming
            import sys

            if sys.platform == "win32":
                prog_name = f"{target.name}.exe"
            else:
                prog_name = target.name

        prog_path = build_dir / prog_name

        prog_node = FileNode(prog_path, defined_at=get_caller_location())
        prog_node.depends(target.object_nodes)

        # Add dependency output nodes from linked targets
        dep_libs = self._collect_dependency_outputs(target)
        if dep_libs:
            prog_node.depends(dep_libs)

        # Add auxiliary input files as dependencies
        auxiliary_inputs = getattr(target, "_auxiliary_inputs", [])
        if auxiliary_inputs:
            linker_input_nodes = [node for node, _ in auxiliary_inputs]
            prog_node.depends(linker_input_nodes)

        # Compute effective link requirements
        effective_link = compute_effective_requirements(
            target, env, for_compilation=False
        )

        # Add auxiliary input flags to link flags
        link_flags = list(effective_link.link_flags)
        for _, flag in auxiliary_inputs:
            link_flags.append(flag)

        # Create context object from effective requirements
        # We need to update link_flags in effective_link before creating context
        effective_link.link_flags = link_flags
        context = CompileLinkContext.from_effective_requirements(effective_link)

        # Determine linker tool based on language - we use 'link' tool
        # but track the language for description purposes
        link_language = "cxx" if "cxx" in target.get_all_languages() else "c"

        prog_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": link_language,
            "sources": target.object_nodes,
            "context": context,
        }

        target.output_nodes.append(prog_node)
        target.nodes.append(prog_node)
        env.register_node(prog_node)

    def _collect_dependency_outputs(self, target: Target) -> list[FileNode]:
        """Collect output nodes from all dependencies.

        Args:
            target: The target whose dependencies to collect.

        Returns:
            List of FileNode outputs from dependencies.
        """
        result: list[FileNode] = []
        for dep in target.transitive_dependencies():
            result.extend(dep.output_nodes)
        return result


class ArchiveNodeFactory:
    """Factory for creating archive (tar/zip) output nodes.

    Handles creation of nodes for Tarfile and Zipfile targets,
    which bundle source files into archives.

    Attributes:
        project: The project being resolved.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory.

        Args:
            project: The project to resolve.
        """
        self.project = project

    def create_archive_node(self, target: Target, sources: list[FileNode]) -> None:
        """Create archive output node for a Tarfile or Zipfile target.

        Args:
            target: The archive target.
            sources: Resolved source file nodes.
        """
        build_info = target._build_info
        if build_info is None:
            return

        output_path = Path(build_info["output"])

        # Create the archive output node
        archive_node = FileNode(output_path, defined_at=get_caller_location())
        archive_node.depends(sources)

        # Copy build info to the node and add sources
        archive_node._build_info = {
            **build_info,
            "sources": sources,
        }

        # Add to target's output nodes
        target.output_nodes.append(archive_node)
        target.nodes.append(archive_node)

        # Register with project
        if output_path not in self.project._nodes:
            self.project._nodes[output_path] = archive_node


class InstallNodeFactory:
    """Factory for creating install/copy nodes.

    Handles creation of nodes for Install and InstallAs targets,
    which copy files to destination directories.

    Attributes:
        project: The project being resolved.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory.

        Args:
            project: The project to resolve.
        """
        self.project = project

    def create_install_nodes(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copy nodes for Install target.

        Args:
            target: The Install target.
            sources: Resolved source file nodes.
            dest_dir: Destination directory.
        """
        import sys

        # Use pcons helper for cross-platform copy (handles forward slashes on Windows)
        python_cmd = sys.executable.replace("\\", "/")
        copy_cmd = f"{python_cmd} -m pcons.util.commands copy"

        installed_nodes: list[FileNode] = []
        for file_node in sources:
            if not isinstance(file_node, FileNode):
                continue

            # Destination path
            dest_path = dest_dir / file_node.path.name

            # Create destination node
            dest_node = FileNode(dest_path, defined_at=get_caller_location())
            dest_node.depends([file_node])

            # Store build info for the copy command
            dest_node._build_info = {
                "tool": "copy",
                "command": copy_cmd,
                "command_var": "copycmd",
                "sources": [file_node],
                "copy_cmd": f"{copy_cmd} $in $out",
            }

            installed_nodes.append(dest_node)

            # Register the node with the project
            if dest_path not in self.project._nodes:
                self.project._nodes[dest_path] = dest_node

        # Add installed files as output nodes (consistent with other builders)
        target._install_nodes = installed_nodes
        target.output_nodes.extend(installed_nodes)

    def create_install_as_node(
        self, target: Target, sources: list[FileNode], dest: Path
    ) -> None:
        """Create copy node for InstallAs target.

        Args:
            target: The InstallAs target.
            sources: Resolved source file nodes (should have exactly one).
            dest: Destination path (full path including filename).
        """
        import sys

        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallAs expects exactly one source, got {len(sources)}. "
                f"Use Install() for multiple files.",
                location=target.defined_at,
            )

        # Use pcons helper for cross-platform copy (handles forward slashes on Windows)
        python_cmd = sys.executable.replace("\\", "/")
        copy_cmd = f"{python_cmd} -m pcons.util.commands copy"

        source_node = sources[0]

        # Create destination node
        dest_node = FileNode(dest, defined_at=get_caller_location())
        dest_node.depends([source_node])

        dest_node._build_info = {
            "tool": "copy",
            "command": copy_cmd,
            "command_var": "copycmd",
            "sources": [source_node],
            "copy_cmd": f"{copy_cmd} $in $out",
        }

        # Add installed file as output node (consistent with other builders)
        target._install_nodes = [dest_node]
        target.output_nodes.append(dest_node)

        if dest not in self.project._nodes:
            self.project._nodes[dest] = dest_node


class Resolver:
    """Resolves targets: computes effective flags and creates nodes.

    The resolver processes all targets in build order (dependencies first),
    computing effective requirements and creating the necessary nodes for
    compilation and linking.

    The resolver delegates to specialized factory classes:
    - ObjectNodeFactory: Creates and caches object nodes
    - OutputNodeFactory: Creates library and program output nodes
    - InstallNodeFactory: Creates install/copy nodes

    Attributes:
        project: The project being resolved.
        _object_factory: Factory for creating object nodes.
        _output_factory: Factory for creating output nodes.
        _install_factory: Factory for creating install nodes.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the resolver.

        Args:
            project: The project to resolve.
        """
        self.project = project
        self._object_factory = ObjectNodeFactory(project)
        self._output_factory = OutputNodeFactory(project)
        self._install_factory = InstallNodeFactory(project)
        self._archive_factory = ArchiveNodeFactory(project)

    # Expose object cache for backwards compatibility
    @property
    def _object_cache(self) -> dict[tuple[Path, tuple], FileNode]:
        """Object cache (delegated to ObjectNodeFactory)."""
        return self._object_factory._object_cache

    def resolve(self) -> None:
        """Resolve all targets in build order.

        Processes targets in dependency order, ensuring all dependencies
        are resolved before their dependents.
        """
        for target in self._targets_in_build_order():
            if not target._resolved:
                self._resolve_target(target)

    def _targets_in_build_order(self) -> list[Target]:
        """Get targets in the order they should be resolved.

        Dependencies come before dependents.

        Returns:
            List of targets in build order.
        """
        return topological_sort_targets(self.project.targets)

    def _resolve_target(self, target: Target) -> None:
        """Resolve a single target.

        Steps:
        1. Compute effective requirements
        2. Create object nodes for each source (via ObjectNodeFactory)
        3. Create output node (library/program) (via OutputNodeFactory)
        4. Set up node dependencies

        Args:
            target: The target to resolve.
        """
        if target._resolved:
            return

        # Get environment for this target
        env = target._env
        if env is None:
            # No environment set - this target can't be resolved
            # (might be an imported target or interface-only)
            if target.target_type == "interface":
                target._resolved = True
                return
            # For other types without env, skip silently
            return

        # Compute effective requirements for compilation
        effective = compute_effective_requirements(target, env, for_compilation=True)

        # Get additional compile flags for this target type from the toolchain
        # (e.g., -fPIC for shared libraries on Linux)
        toolchain = env._toolchain
        if toolchain is not None and target.target_type is not None:
            target_type_flags = toolchain.get_compile_flags_for_target_type(
                str(target.target_type)
            )
            # Merge target-type-specific flags into effective requirements
            for flag in target_type_flags:
                if flag not in effective.compile_flags:
                    effective.compile_flags.append(flag)

        # Determine the language for this target based on sources
        language = self._determine_language(target, env)
        if language:
            target.required_languages.add(language)

        # Separate sources into compilable sources and auxiliary inputs
        # Auxiliary inputs are files like .def that are passed directly to a downstream tool
        auxiliary_inputs: list[tuple[FileNode, str]] = []  # (file_node, flag)

        # Create object nodes for each source (delegated to factory)
        for source in target.sources:
            if isinstance(source, FileNode):
                # Check if this is an auxiliary input file
                aux_handler = self._object_factory.get_auxiliary_input_handler(
                    source.path, env
                )
                if aux_handler is not None:
                    # This is an auxiliary input - generate the flag
                    flag = aux_handler.flag_template.replace("$file", str(source.path))
                    auxiliary_inputs.append((source, flag))
                    continue

                # Normal source file - create object node
                obj_node = self._object_factory.create_object_node(
                    target, source, effective, env
                )
                if obj_node:
                    target.object_nodes.append(obj_node)

        # Store auxiliary inputs on the target for use by output factories
        target._auxiliary_inputs = auxiliary_inputs

        # Create output node(s) based on target type (delegated to factory)
        if target.target_type == "static_library":
            self._output_factory.create_static_library_output(target, env)
        elif target.target_type == "shared_library":
            self._output_factory.create_shared_library_output(target, env)
        elif target.target_type == "program":
            self._output_factory.create_program_output(target, env)
        elif target.target_type == "interface":
            # Interface targets have no outputs
            pass
        elif target.target_type == "object":
            # Object-only targets: output_nodes are the object files
            target.output_nodes = list(target.object_nodes)
            target.nodes = list(target.object_nodes)

        target._resolved = True

    def _determine_language(self, target: Target, env: Environment) -> str | None:
        """Determine the primary language for a target based on its sources.

        Uses all toolchains to determine language (tool-agnostic), falling back
        to hardcoded suffixes if no toolchain handles the source.

        Args:
            target: The target to analyze.
            env: Environment containing the toolchain(s).

        Returns:
            Language name ('c', 'cxx', etc.) or None.
        """
        languages: set[str] = set()

        for source in target.sources:
            if isinstance(source, FileNode):
                # Try all toolchains (tool-agnostic)
                handler_found = False
                for toolchain in env.toolchains:
                    handler = toolchain.get_source_handler(source.path.suffix)
                    if handler:
                        languages.add(handler.language)
                        handler_found = True
                        break  # First handler wins for this source

                if handler_found:
                    continue

                # Fallback to hardcoded suffixes
                suffix = source.path.suffix.lower()
                if suffix in (".cpp", ".cxx", ".cc", ".c++", ".mm"):
                    logger.warning(
                        "Using deprecated hardcoded suffix fallback for '%s'. "
                        "Consider configuring a toolchain that handles this suffix.",
                        source.path.suffix,
                    )
                    languages.add("cxx")
                elif source.path.suffix == ".C":  # Case-sensitive
                    logger.warning(
                        "Using deprecated hardcoded suffix fallback for '.C'. "
                        "Consider configuring a toolchain that handles this suffix.",
                    )
                    languages.add("cxx")
                elif suffix in (".c", ".m"):
                    logger.warning(
                        "Using deprecated hardcoded suffix fallback for '%s'. "
                        "Consider configuring a toolchain that handles this suffix.",
                        source.path.suffix,
                    )
                    languages.add("c")

        # Return highest priority language
        if "cxx" in languages or "objcxx" in languages:
            return "cxx"
        if "c" in languages or "objc" in languages:
            return "c"
        return None

    # Delegate methods to factories for backwards compatibility
    def _get_object_path(self, target: Target, source: Path, env: Environment) -> Path:
        """Generate target-specific output path for an object file.

        Delegated to ObjectNodeFactory.
        """
        return self._object_factory.get_object_path(target, source, env)

    def _get_source_handler(
        self, source: Path, env: Environment
    ) -> SourceHandler | None:
        """Get source handler from the environment's toolchain.

        Delegated to ObjectNodeFactory.
        """
        return self._object_factory.get_source_handler(source, env)

    def _get_tool_for_source(
        self, source: Path, env: Environment
    ) -> tuple[str, str] | None:
        """Return (tool_name, language) based on source suffix.

        Delegated to ObjectNodeFactory.
        """
        return self._object_factory.get_tool_for_source(source, env)

    def _create_object_node(
        self,
        target: Target,
        source: FileNode,
        effective: EffectiveRequirements,
        env: Environment,
    ) -> FileNode | None:
        """Create object file node with effective requirements in build_info.

        Delegated to ObjectNodeFactory.
        """
        return self._object_factory.create_object_node(target, source, effective, env)

    def _create_static_library_output(self, target: Target, env: Environment) -> None:
        """Create static library output node.

        Delegated to OutputNodeFactory.
        """
        self._output_factory.create_static_library_output(target, env)

    def _create_shared_library_output(self, target: Target, env: Environment) -> None:
        """Create shared library output node.

        Delegated to OutputNodeFactory.
        """
        self._output_factory.create_shared_library_output(target, env)

    def _create_program_output(self, target: Target, env: Environment) -> None:
        """Create program output node.

        Delegated to OutputNodeFactory.
        """
        self._output_factory.create_program_output(target, env)

    def _collect_dependency_outputs(self, target: Target) -> list[FileNode]:
        """Collect output nodes from all dependencies.

        Delegated to OutputNodeFactory.
        """
        return self._output_factory._collect_dependency_outputs(target)

    def resolve_pending_sources(self) -> None:
        """Resolve _pending_sources for all targets that have them.

        Called after main resolution so output_nodes are populated.
        This handles Install, InstallAs, and similar targets that need
        to reference outputs from other targets.
        """
        for target in self._targets_in_build_order():
            if target._pending_sources is not None:
                self._resolve_target_pending_sources(target)

    def _resolve_target_pending_sources(self, target: Target) -> None:
        """Resolve pending sources for a single target.

        Recursively ensures any source targets have their pending sources
        resolved first, then creates the appropriate nodes.
        """
        from pathlib import Path

        from pcons.core.node import FileNode, Node
        from pcons.core.target import Target

        if target._pending_sources is None:
            return

        # Recursively resolve any source targets that also have pending sources
        for source in target._pending_sources:
            if isinstance(source, Target) and source._pending_sources is not None:
                self._resolve_target_pending_sources(source)

        # Now collect resolved source files
        resolved_sources: list[FileNode] = []
        for source in target._pending_sources:
            if isinstance(source, Target):
                # Get output files from the now-resolved target
                resolved_sources.extend(source.output_nodes)
                # Also check nodes directly (for interface targets)
                for node in source.nodes:
                    if isinstance(node, FileNode) and node not in resolved_sources:
                        resolved_sources.append(node)
            elif isinstance(source, FileNode):
                resolved_sources.append(source)
            elif isinstance(source, Node):
                # Skip non-file nodes
                pass
            elif isinstance(source, (Path, str)):
                resolved_sources.append(self.project.node(source))

        # Create nodes based on target type
        if target._install_dest_dir is not None:
            # This is an Install target
            self._install_factory.create_install_nodes(
                target, resolved_sources, target._install_dest_dir
            )
        elif target._install_as_dest is not None:
            # This is an InstallAs target
            self._install_factory.create_install_as_node(
                target, resolved_sources, target._install_as_dest
            )
        elif target.target_type == "archive":
            # This is a Tarfile or Zipfile target
            self._archive_factory.create_archive_node(target, resolved_sources)

        # Mark as processed
        target._pending_sources = None

    # Delegate methods to InstallNodeFactory for backwards compatibility
    def _create_install_nodes(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copy nodes for Install target.

        Delegated to InstallNodeFactory.
        """
        self._install_factory.create_install_nodes(target, sources, dest_dir)

    def _create_install_as_node(
        self, target: Target, sources: list[FileNode], dest: Path
    ) -> None:
        """Create copy node for InstallAs target.

        Delegated to InstallNodeFactory.
        """
        self._install_factory.create_install_as_node(target, sources, dest)
