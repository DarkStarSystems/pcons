# SPDX-License-Identifier: MIT
"""Target resolution system for target-centric builds.

The Resolver is responsible for:
1. Computing effective requirements for each target
2. Creating object nodes for source files with the effective flags
3. Creating output nodes (libraries, programs) with proper dependencies
4. Object file caching (same source + same flags = shared object)
5. Expanding command templates to fully-resolved commands

The resolver is designed to be tool-agnostic: it delegates source handling
to the toolchain rather than having hardcoded knowledge about file types.

The resolution logic is organized into focused factory classes:
- ObjectNodeFactory: Creates and caches object nodes for source files
- OutputNodeFactory: Creates library and program output nodes
- InstallNodeFactory: Creates install/copy nodes for install targets

Command expansion happens in the resolver so that generators receive
fully-expanded commands with $SOURCE/$TARGET placeholders. Each generator
then converts these to its native syntax (Ninja: $in/$out, Makefile: paths).
This ensures consistent command handling across all generators.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.debug import is_enabled, trace, trace_value
from pcons.core.graph import topological_sort_targets
from pcons.core.node import FileNode
from pcons.core.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)
from pcons.core.subst import PathToken, TargetPath
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

    def _resolve_depfile(
        self, depfile_spec: TargetPath | None, target_path: Path
    ) -> PathToken | None:
        """Resolve depfile specification to a PathToken.

        Converts TargetPath markers to PathToken with the actual target path.

        Args:
            depfile_spec: The depfile specification from SourceHandler.
                - TargetPath(suffix=".d"): Convert to PathToken
                - None: No depfile
            target_path: The actual target output path.

        Returns:
            PathToken for TargetPath, None for None.
        """
        if depfile_spec is None:
            return None

        # Convert TargetPath to PathToken with actual target path
        # The depfile path is the target path with the suffix appended
        return PathToken(
            prefix=depfile_spec.prefix,
            path=str(target_path),
            path_type="build",  # Depfiles are in the build directory
            suffix=depfile_spec.suffix,
        )

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
            FileNode for the object file, or the source directly if not compilable
            (e.g., pre-compiled .o files, linker scripts).
        """
        # Get source handler from the toolchain (tool-agnostic)
        handler = self.get_source_handler(source.path, env)
        if handler is None:
            # No handler = not a compilable source
            # Pass through directly (e.g., pre-compiled .o files, linker scripts)
            return source

        tool_name = handler.tool_name
        language = handler.language
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

        # Resolve depfile: convert TargetPath to PathToken with actual target path
        depfile = self._resolve_depfile(handler.depfile, obj_path)

        # Propagate explicit dependencies from source to object as implicit deps.
        # This allows generated headers (added via source.depends()) to trigger
        # rebuilds without being explicit compiler inputs.
        if source.explicit_deps:
            obj_node.implicit_deps.extend(source.explicit_deps)

        # Create context object from effective requirements
        context = CompileLinkContext.from_effective_requirements(effective)

        # Store build info for the generator
        # Use handler's depfile/deps_style (from toolchain, not hardcoded)
        # Store env reference so command expansion can access tool configuration
        obj_node._build_info = {
            "tool": tool_name,
            "command_var": command_var,
            "language": language,
            "sources": [source],
            "depfile": depfile,
            "deps_style": deps_style,
            "context": context,
            "env": env,
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
            "env": env,
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

        # Determine linker language - we use 'link' tool for linking
        # but track the language so toolchain context can set appropriate linker
        link_language = "cxx" if "cxx" in target.get_all_languages() else "c"

        # Create context object from effective requirements
        # We need to update link_flags in effective_link before creating context
        # Pass language and env so the context can determine the appropriate linker
        effective_link.link_flags = link_flags
        context = CompileLinkContext.from_effective_requirements(
            effective_link, language=link_language, env=env
        )

        lib_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": link_language,
            "sources": target.object_nodes,
            "context": context,
            "env": env,
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

        # Determine linker language - we use 'link' tool for linking
        # but track the language so toolchain context can set appropriate linker
        link_language = "cxx" if "cxx" in target.get_all_languages() else "c"

        # Create context object from effective requirements
        # We need to update link_flags in effective_link before creating context
        # Pass language and env so the context can determine the appropriate linker
        effective_link.link_flags = link_flags
        context = CompileLinkContext.from_effective_requirements(
            effective_link, language=link_language, env=env
        )

        prog_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": link_language,
            "sources": target.object_nodes,
            "context": context,
            "env": env,
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
        are resolved before their dependents. After all targets are resolved,
        expands command templates so generators receive fully-expanded commands.
        """
        trace("resolve", "Starting resolution phase")
        trace_value("resolve", "total_targets", len(self.project.targets))

        for target in self._targets_in_build_order():
            if not target._resolved:
                self._resolve_target(target)

        # Expand command templates for all nodes
        trace("resolve", "Starting command expansion")
        self._expand_node_commands()
        trace("resolve", "Resolution complete")

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

        trace("resolve", "Resolving target: %s", target.name)

        # Get environment for this target
        env = target._env
        if env is None:
            # No environment set - this target can't be resolved
            # (might be an imported target or interface-only)
            if target.target_type == "interface":
                trace("resolve", "  Skipping interface target (no env)")
                target._resolved = True
                return
            # For other types without env, skip silently
            trace("resolve", "  Skipping target without env")
            return

        if is_enabled("resolve"):
            trace_value("resolve", "defined_at", target.defined_at)
            trace_value("resolve", "type", target.target_type)
            trace_value("resolve", "sources", [str(s.name) for s in target.sources])
            trace_value(
                "resolve", "dependencies", [d.name for d in target.dependencies]
            )

        # Compute effective requirements for compilation
        effective = compute_effective_requirements(target, env, for_compilation=True)

        if is_enabled("resolve"):
            trace("resolve", "  Effective requirements:")
            trace_value("resolve", "includes", [str(p) for p in effective.includes])
            trace_value("resolve", "defines", effective.defines)
            trace_value("resolve", "compile_flags", effective.compile_flags)

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
        trace("resolve", "  Creating object nodes for %d sources", len(target.sources))
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
                    trace("resolve", "    %s -> auxiliary input", source.path)
                    continue

                # Normal source file - create object node
                obj_node = self._object_factory.create_object_node(
                    target, source, effective, env
                )
                if obj_node:
                    target.object_nodes.append(obj_node)
                    trace("resolve", "    %s -> %s", source.path, obj_node.path)

        # Store auxiliary inputs on the target for use by output factories
        target._auxiliary_inputs = auxiliary_inputs

        # Create output node(s) based on target type (delegated to factory)
        trace("resolve", "  Creating output for type: %s", target.target_type)
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

        if target.output_nodes:
            trace("resolve", "  Output: %s", [str(n.path) for n in target.output_nodes])

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
        to reference outputs from other targets. After resolving all
        pending sources, expands command templates for any new nodes.
        """
        for target in self._targets_in_build_order():
            if target._pending_sources is not None:
                self._resolve_target_pending_sources(target)

        # Expand command templates for any new nodes created during pending resolution
        self._expand_node_commands()

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

    def _expand_node_commands(self) -> None:
        """Expand command templates for all nodes with _build_info.

        This method is called at the end of resolution to expand all command
        templates so generators receive fully-expanded commands with
        $SOURCE/$TARGET as placeholders (converted by generators to native syntax).

        For each node with _build_info containing 'tool' and 'command_var'
        but no 'command', this method:
        1. Gets the command template from env.<tool>.<command_var>
        2. If context has get_env_overrides(), sets those values on the tool
        3. Calls env.subst() to expand the template
        4. Stores the result in _build_info["command"]
        """

        # Collect all nodes with _build_info from targets
        nodes_to_expand: list[FileNode] = []

        for target in self.project.targets:
            # Collect object nodes
            for node in target.object_nodes:
                if isinstance(node, FileNode) and hasattr(node, "_build_info"):
                    nodes_to_expand.append(node)
            # Collect output nodes
            for node in target.output_nodes:
                if isinstance(node, FileNode) and hasattr(node, "_build_info"):
                    nodes_to_expand.append(node)
            # Collect other nodes
            for node in target.nodes:
                if isinstance(node, FileNode) and hasattr(node, "_build_info"):
                    if node not in nodes_to_expand:
                        nodes_to_expand.append(node)

        # Also check nodes tracked in environments
        for env in self.project.environments:
            for node in getattr(env, "_created_nodes", []):
                if isinstance(node, FileNode) and hasattr(node, "_build_info"):
                    if node not in nodes_to_expand:
                        nodes_to_expand.append(node)

        # Expand commands for each node
        for node in nodes_to_expand:
            self._expand_single_node_command(node)

    def _expand_single_node_command(self, node: FileNode) -> None:
        """Expand command template for a single node.

        Args:
            node: FileNode with _build_info to expand.
        """
        from pcons.core.environment import Environment

        build_info = getattr(node, "_build_info", None)
        if build_info is None:
            return

        # Skip if already has expanded command
        if "command" in build_info:
            return

        # Get required fields
        tool_name = build_info.get("tool")
        command_var = build_info.get("command_var")
        if tool_name is None or command_var is None:
            return

        trace("subst", "Expanding command for node: %s", node.path)
        trace_value("subst", "tool", tool_name)
        trace_value("subst", "command_var", command_var)

        # Get env from build_info
        env = build_info.get("env")
        if env is None or not isinstance(env, Environment):
            logger.debug(
                "Node %s has no env in _build_info, skipping command expansion",
                node.path,
            )
            return

        # Get context if present
        context = build_info.get("context")

        # Get the command template from the environment
        tool_config = getattr(env, tool_name, None)
        if tool_config is None:
            logger.warning(
                "Tool '%s' not found in environment for node %s",
                tool_name,
                node.path,
            )
            return

        cmd_template = getattr(tool_config, command_var, None)
        if cmd_template is None:
            logger.warning(
                "Command template '%s.%s' not found for node %s",
                tool_name,
                command_var,
                node.path,
            )
            return

        # Check if context has get_env_overrides() - for all contexts (archive/install
        # and compile/link). Build tool-specific overrides as extra_vars for subst().
        # This allows templates like ${prefix(cc.iprefix, cc.includes)} to expand
        # with the effective requirements.
        #
        # IMPORTANT: We must NOT mutate the shared tool_config, as that would cause
        # flags to accumulate across multiple source files in the same target.
        # Instead, we build a dictionary of namespaced overrides that get passed
        # to subst_list() via extra_vars.
        #
        # Determine if this is a compile command (objcmd) vs link command
        is_compile_command = command_var == "objcmd"
        is_link_command = command_var in ("progcmd", "sharedcmd", "linkcmd", "libcmd")

        # Build tool-specific overrides as a dictionary for subst()
        # These will be passed as extra_vars and take precedence over tool_config
        tool_overrides: dict[str, object] = {}

        if context is not None and hasattr(context, "get_env_overrides"):
            context_overrides = context.get_env_overrides()
            if is_enabled("subst") and context_overrides:
                trace("subst", "  Context overrides:")
                for k, v in context_overrides.items():
                    trace_value("subst", k, v)
            for key, val in context_overrides.items():
                if key == "extra_flags":
                    # extra_flags are compile flags - only apply to compile commands
                    if is_compile_command:
                        # Merge extra_flags into the tool's flags list
                        # Templates use $cc.flags, not $cc.extra_flags
                        existing_flags = getattr(tool_config, "flags", [])
                        if isinstance(existing_flags, list):
                            merged = list(existing_flags) + list(val)
                        else:
                            merged = list(val)
                        # Store as namespaced key for subst() lookup
                        tool_overrides[f"{tool_name}.flags"] = merged
                elif key == "ldflags":
                    # ldflags are link flags - only apply to link commands
                    if is_link_command:
                        # Merge ldflags into the link tool's flags list
                        existing_flags = getattr(tool_config, "flags", [])
                        if isinstance(existing_flags, list):
                            merged = list(existing_flags) + list(val)
                        else:
                            merged = list(val)
                        # Store as namespaced key for subst() lookup
                        tool_overrides[f"{tool_name}.flags"] = merged
                elif key == "linker_cmd":
                    # linker_cmd overrides link.cmd (e.g., clang++ for C++ linking)
                    if is_link_command:
                        tool_overrides[f"{tool_name}.cmd"] = val
                else:
                    # Set as namespaced key (includes, defines, libs, libdirs)
                    tool_overrides[f"{tool_name}.{key}"] = val

        # Use typed marker objects for generator-agnostic path references
        # SourcePath/TargetPath are preserved through subst() and converted
        # to generator-specific syntax by each generator (e.g., $in/$out for Ninja)
        from pcons.core.subst import SourcePath

        extra_vars: dict[str, object] = {}
        extra_vars["SOURCE"] = SourcePath()
        extra_vars["SOURCES"] = SourcePath()  # Generator handles single vs. multiple
        extra_vars["TARGET"] = TargetPath()
        extra_vars["TARGETS"] = TargetPath()  # Generator handles single vs. multiple

        # Add tool-specific overrides from context (includes, defines, flags, libs, etc.)
        # These take precedence over tool_config values in namespace lookup
        extra_vars.update(tool_overrides)

        # Expand the command template to a list of tokens
        # Tokens stay separate for proper quoting - generator joins with shell quoting
        # If substitution fails due to missing variables (e.g., custom toolchain vars),
        # leave the command unexpanded for the generator's fallback logic
        from pcons.core.errors import MissingVariableError

        try:
            command_tokens = env.subst_list(cmd_template, **extra_vars)
        except MissingVariableError as e:
            logger.debug(
                "Command expansion failed for node %s: %s. "
                "Generator will use fallback expansion.",
                node.path,
                e,
            )
            return  # Don't set command, let generator handle it

        # Store expanded command tokens in build_info
        # All context variables (includes, defines, flags, libs, etc.) are now
        # fully expanded into the token list via get_env_overrides()
        # Generator will join tokens with shell-appropriate quoting
        build_info["command"] = command_tokens
        trace(
            "subst",
            "  Expanded command: %s",
            command_tokens[:10] if len(command_tokens) > 10 else command_tokens,
        )
