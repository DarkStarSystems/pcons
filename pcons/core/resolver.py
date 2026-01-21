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
from typing import TYPE_CHECKING, Any

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.graph import topological_sort_targets
from pcons.core.node import FileNode
from pcons.core.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)
from pcons.toolchains.build_context import CompileLinkContext
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


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

        Format: <build_dir>/obj.<target_name>/<source_stem>.<suffix>

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
        # Get source handler from the toolchain (tool-agnostic)
        handler = self.get_source_handler(source.path, env)
        if handler is None:
            return None

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

        # Propagate explicit dependencies from source to object as implicit deps.
        # This allows generated headers (added via source.depends()) to trigger
        # rebuilds without being explicit compiler inputs.
        if source.explicit_deps:
            obj_node.implicit_deps.extend(source.explicit_deps)

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
        path_resolver = self.project.path_resolver

        # Check for custom output name first
        if target.output_name:
            # User provided custom output - normalize it
            lib_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_static_library_name(target.name)
            lib_path = build_dir / lib_name
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
        path_resolver = self.project.path_resolver

        # Check for custom output name first
        if target.output_name:
            # User provided custom output - normalize it
            lib_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_shared_library_name(target.name)
            lib_path = build_dir / lib_name
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
        path_resolver = self.project.path_resolver

        # Check for custom output name first
        if target.output_name:
            # User provided custom output - normalize it
            prog_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            prog_name = toolchain.get_program_name(target.name)
            prog_path = build_dir / prog_name
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


class Resolver:
    """Resolves targets: computes effective flags and creates nodes.

    The resolver processes all targets in build order (dependencies first),
    computing effective requirements and creating the necessary nodes for
    compilation and linking.

    Resolution is delegated to factory classes:
    - ObjectNodeFactory: Creates and caches object nodes for compilation
    - OutputNodeFactory: Creates library and program output nodes
    - Builder factories (from BuilderRegistry): Handle pending source resolution
      for Install, Tarfile, and other registered builders

    Attributes:
        project: The project being resolved.
        _object_factory: Factory for creating object nodes.
        _output_factory: Factory for creating output nodes.
        _builder_factories: Factory instances from registered builders.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the resolver.

        Args:
            project: The project to resolve.
        """
        self.project = project
        self._object_factory = ObjectNodeFactory(project)
        self._output_factory = OutputNodeFactory(project)

        # Build factory dispatch table from BuilderRegistry
        # This allows registered builders to provide custom resolution factories
        # All built-in builders (Install, Tarfile, etc.) register their factories here
        self._builder_factories: dict[str, Any] = {}
        for name, registration in BuilderRegistry.all().items():
            if registration.factory_class is not None:
                self._builder_factories[name] = registration.factory_class(project)

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

        Uses all toolchains to determine language (tool-agnostic).

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
                for toolchain in env.toolchains:
                    handler = toolchain.get_source_handler(source.path.suffix)
                    if handler:
                        languages.add(handler.language)
                        break  # First handler wins for this source

        # Return highest priority language
        if "cxx" in languages or "objcxx" in languages:
            return "cxx"
        if "c" in languages or "objc" in languages:
            return "c"
        return None

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

        Uses factory dispatch via _builder_name from the BuilderRegistry.
        All built-in builders (Install, InstallAs, InstallDir, Tarfile, Zipfile)
        are registered there with their factory classes.
        """
        from pcons.core.target import Target

        if target._pending_sources is None:
            return

        # Recursively resolve any source targets that also have pending sources
        for source in target._pending_sources:
            if isinstance(source, Target) and source._pending_sources is not None:
                self._resolve_target_pending_sources(source)

        # Use factory dispatch via _builder_name
        builder_name = target._builder_name
        if builder_name is not None and builder_name in self._builder_factories:
            factory = self._builder_factories[builder_name]
            factory.resolve_pending(target)
            target._pending_sources = None
            return

        # No factory found - log a warning if there are pending sources
        if target._pending_sources:
            logger.warning(
                "Target '%s' has pending sources but no factory registered for "
                "builder '%s'. Sources will not be resolved.",
                target.name,
                builder_name,
            )

        # Mark as processed
        target._pending_sources = None
