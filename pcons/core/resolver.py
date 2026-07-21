# SPDX-License-Identifier: MIT
"""Target resolution system — tool-agnostic factory dispatch.

The Resolver turns high-level Target descriptions into concrete build nodes
in three phases:

1. **Main resolution** (resolve() -> _resolve_target()): for each target in
   dependency order, dispatch to its registered factory (via BuilderRegistry)
   to create build nodes. All tool-specific logic lives in the factories
   (CompileLinkFactory, InstallNodeFactory, ArchiveNodeFactory,
   CommandNodeFactory below).
2. **Pending source resolution** (resolve_pending_sources()): targets that
   reference other targets' outputs (Install, Tarfile, ...) store those
   sources as "pending" and resolve them here, after output_nodes exist.
3. **Command expansion** (_expand_node_commands()): expand each node's
   command template (env.<tool>.<command_var> plus ToolchainContext
   overrides) into node._build_info["command"].

Targets are just descriptions until resolve() is called, so output_name,
flags, etc. can be customized after target creation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.debug import is_enabled, trace, trace_value
from pcons.core.errors import DependencyCycleError
from pcons.core.graph import topological_sort_targets
from pcons.core.node import FileNode, Node
from pcons.core.subst import TargetPath

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


class PendingSourceFactory:
    """Base factory for targets that resolve pending sources in phase 2.

    A "pending source" is a source that references another Target whose
    output_nodes aren't available yet during phase 1, so resolution is
    deferred until phase 2 when all targets have been resolved.
    """

    def __init__(self, project: Project) -> None:
        self.project = project

    def resolve(
        self,
        target: Target,  # noqa: ARG002
        env: Environment | None,  # noqa: ARG002
    ) -> None:
        """Phase 1 — no-op by default."""

    def resolve_pending(self, target: Target) -> None:
        """Phase 2 — resolve pending sources. Subclasses must override."""
        raise NotImplementedError

    def _resolve_sources(self, target: Target) -> list[FileNode]:
        """Resolve pending sources to FileNodes.

        Handles Target sources (extracts output_nodes — final products
        only, not intermediates), FileNode passthrough, and Path/str
        sources (creates nodes via project).
        """
        from pcons.core.target import Target as TargetClass

        if target._pending_sources is None:
            return []

        resolved: list[FileNode] = []
        for source in target._pending_sources:
            if isinstance(source, TargetClass):
                resolved.extend(source.output_nodes)
            elif isinstance(source, FileNode):
                resolved.append(source)
            elif isinstance(source, Node):
                pass
            elif isinstance(source, (Path, str)):
                resolved.append(self.project.node(source))
        return resolved


class NoOpFactory(PendingSourceFactory):
    """Factory for targets that need no resolution (e.g., interface targets)."""

    def resolve_pending(self, target: Target) -> None:
        """No-op: nothing to resolve."""


class CommandNodeFactory(PendingSourceFactory):
    """Factory for resolving Command target pending sources.

    Command targets (created by env.Command) already have output_nodes
    from GenericCommandBuilder. This factory wires up pending source
    dependencies and updates build_info.
    """

    def resolve_pending(self, target: Target) -> None:
        """Add each source Target's outputs as dependencies of the command's
        output nodes and include them in _build_info's sources."""
        additional_sources = self._resolve_sources(target)

        if not additional_sources:
            return

        # Add as dependencies to command's output nodes
        for node in target.output_nodes:
            node.depends(additional_sources)

        # Update _build_info to include additional sources
        if target.output_nodes:
            primary = target.output_nodes[0]
            if hasattr(primary, "_build_info") and primary._build_info:
                existing_sources = primary._build_info.get("sources", [])
                primary._build_info["sources"] = (
                    list(existing_sources) + additional_sources
                )


class Resolver:
    """Resolves targets: computes effective flags and creates nodes.

    Processes all targets in build order (dependencies first), dispatching
    each — uniformly, whatever its type — to its registered factory. See
    the module docstring for the phase overview.
    """

    def __init__(self, project: Project) -> None:
        self.project = project

        # Factory dispatch table from BuilderRegistry
        self._builder_factories: dict[str, Any] = {}
        for name, registration in BuilderRegistry.all().items():
            if registration.factory_class is not None:
                self._builder_factories[name] = registration.factory_class(project)

        # Register Command factory (env.Command doesn't use builder registry)
        self._builder_factories["Command"] = CommandNodeFactory(project)

        # Stack of qualified_names currently being resolved. _resolve_target()
        # recurses eagerly into target.depends() targets (_implicit_target_deps),
        # which aren't reflected in the topological-order graph (that only
        # covers .dependencies), so a depends()-only cycle isn't caught by
        # _targets_in_build_order()'s cycle detection. This stack lets that
        # recursion detect re-entrancy and raise a clean DependencyCycleError
        # instead of recursing until RecursionError.
        self._resolving: list[str] = []

    def resolve(self) -> None:
        """Resolve all targets in build order, then expand command templates."""
        trace("resolve", "Starting resolution phase")
        trace_value("resolve", "total_targets", len(self.project.targets))

        for target in self._targets_in_build_order():
            if not target._resolved:
                self._resolve_target(target)

        # Call after_resolve hook on all toolchains that support it (e.g., Fortran dyndep).
        # Iterates all toolchains (primary + additional) so secondary toolchains
        # like GfortranToolchain added via env.add_toolchain() are also notified.
        # Collect source_obj_by_language from all factory instances that track it.
        source_obj_by_language: dict[str, list[tuple[Path, FileNode]]] = {}
        for factory in self._builder_factories.values():
            lang_map = getattr(factory, "_source_obj_by_language", None)
            if lang_map:
                for lang, pairs in lang_map.items():
                    source_obj_by_language.setdefault(lang, []).extend(pairs)

        seen_toolchains: set[int] = set()
        for target in self.project.targets:
            if target._env:
                for tc in target._env.toolchains:
                    if id(tc) not in seen_toolchains and hasattr(tc, "after_resolve"):
                        seen_toolchains.add(id(tc))
                        tc.after_resolve(
                            self.project,
                            source_obj_by_language,
                        )

        # Expand command templates for all nodes
        trace("resolve", "Starting command expansion")
        self._expand_node_commands()
        trace("resolve", "Resolution complete")

    def _targets_in_build_order(self) -> list[Target]:
        """Get targets in resolution order (dependencies before dependents)."""
        return topological_sort_targets(self.project.targets)

    def _resolve_target(self, target: Target) -> None:
        """Resolve a single target via its registered factory."""
        if target._resolved:
            return

        qualified_name = target.qualified_name
        if qualified_name in self._resolving:
            # A depends()-only cycle: this target is already being resolved
            # further up the call stack (reached via _implicit_target_deps
            # recursion below), so raise instead of recursing forever.
            cycle_start = self._resolving.index(qualified_name)
            raise DependencyCycleError([*self._resolving[cycle_start:], qualified_name])

        trace("resolve", "Resolving target: %s", target.name)

        self._resolving.append(qualified_name)
        try:
            env = target._env

            # Dispatch to registered factory via _builder_name
            builder_name = target._builder_name
            if builder_name is not None and builder_name in self._builder_factories:
                factory = self._builder_factories[builder_name]
                factory.resolve(target, env)
            elif env is None:
                trace("resolve", "  Skipping target without env")
            else:
                logger.debug(
                    "Target '%s' has no factory registered for builder '%s'",
                    target.name,
                    builder_name,
                )

            if target.output_nodes:
                trace(
                    "resolve",
                    "  Output: %s",
                    [str(n.path) for n in target.output_nodes],
                )

            # Apply any extra implicit deps added via target.depends()
            if target._extra_implicit_deps:
                target._apply_extra_implicit_deps()

            # Apply implicit target dependencies from target.depends(other_target).
            # Propagated deps: outputs become implicit deps on all build nodes
            # (intermediate + output). Output-only deps: only on output nodes.
            for dep_target in target._implicit_target_deps:
                if not dep_target._resolved:
                    self._resolve_target(dep_target)
                for node in target.intermediate_nodes + target.output_nodes:
                    for dep_node in dep_target.output_nodes:
                        if dep_node not in node.implicit_deps:
                            node.implicit_deps.append(dep_node)

            for dep_target in target._implicit_target_deps_output_only:
                if not dep_target._resolved:
                    self._resolve_target(dep_target)
                for node in target.output_nodes:
                    for dep_node in dep_target.output_nodes:
                        if dep_node not in node.implicit_deps:
                            node.implicit_deps.append(dep_node)
        finally:
            self._resolving.pop()

        target._resolved = True

    def resolve_pending_sources(self) -> None:
        """Resolve _pending_sources for all targets that have them.

        Must run after main resolution so output_nodes are populated;
        afterwards expands command templates for any new nodes.
        """
        for target in self._targets_in_build_order():
            if target._pending_sources is not None:
                self._resolve_target_pending_sources(target)

        # Expand command templates for any new nodes created during pending resolution
        self._expand_node_commands()

    def _resolve_target_pending_sources(self, target: Target) -> None:
        """Resolve pending sources for a single target, recursing into
        source targets that themselves have pending sources first."""
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
        """Expand command templates for all nodes with _build_info, leaving
        $SOURCE/$TARGET markers for generators to convert to native syntax."""
        nodes_to_expand: list[FileNode] = []
        seen: set[FileNode] = set()

        def _add_node(node: Node) -> None:
            if (
                node not in seen
                and isinstance(node, FileNode)
                and node._build_info is not None
            ):
                seen.add(node)
                nodes_to_expand.append(node)

        for target in self.project.targets:
            for node in target.intermediate_nodes:
                _add_node(node)
            for node in target.output_nodes:
                _add_node(node)

        for env in self.project.environments:
            for node in getattr(env, "_created_nodes", []):
                _add_node(node)

        for node in nodes_to_expand:
            self._expand_single_node_command(node)

    def _expand_single_node_command(self, node: FileNode) -> None:
        """Expand the command template for a single node."""
        from pcons.core.environment import Environment

        build_info = node._build_info
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

        # Get context overrides and apply them as namespaced tool variables.
        # Each context's get_env_overrides() returns keys that map directly to
        # tool config attributes (e.g., "flags" -> "{tool_name}.flags").
        #
        # IMPORTANT: We must NOT mutate the shared tool_config, as that would cause
        # flags to accumulate across multiple source files in the same target.
        # Instead, we build a dictionary of namespaced overrides that get passed
        # to subst_list() via extra_vars.
        tool_overrides: dict[str, object] = {}

        if context is not None and hasattr(context, "get_env_overrides"):
            context_overrides = context.get_env_overrides()
            if is_enabled("subst") and context_overrides:
                trace("subst", "  Context overrides:")
                for k, v in context_overrides.items():
                    trace_value("subst", k, v)
            for key, val in context_overrides.items():
                tool_overrides[f"{tool_name}.{key}"] = val

        # SourcePath/TargetPath markers are preserved through subst() and
        # converted to generator-specific syntax (e.g. $in/$out for Ninja)
        from pcons.core.subst import SourcePath

        extra_vars: dict[str, object] = {}
        extra_vars["SOURCE"] = SourcePath()
        extra_vars["SOURCES"] = SourcePath()  # Generator handles single vs. multiple
        extra_vars["TARGET"] = TargetPath()
        extra_vars["TARGETS"] = TargetPath()  # Generator handles single vs. multiple

        # Context overrides take precedence over tool_config values
        extra_vars.update(tool_overrides)

        # Per-node template variables (e.g. a grouped compile node's
        # MODULE_NAME, set by a toolchain's setup_group_node hook).
        node_vars = build_info.get("vars")
        if node_vars:
            extra_vars.update(node_vars)

        # Tokens stay separate; the generator joins them with shell quoting
        command_tokens = env.subst_list(cmd_template, **extra_vars)
        build_info["command"] = command_tokens
        trace(
            "subst",
            "  Expanded command: %s",
            command_tokens[:10] if len(command_tokens) > 10 else command_tokens,
        )
