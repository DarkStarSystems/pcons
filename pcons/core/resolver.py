# SPDX-License-Identifier: MIT
"""Target resolution system — tool-agnostic factory dispatch.

The Resolver turns high-level Target descriptions into concrete build nodes
in two phases:

1. **Main resolution** (resolve() -> _resolve_target()): for each target in
   dependency order, dispatch to its registered factory (via BuilderRegistry)
   to create build nodes. All tool-specific logic lives in the factories
   (CompileLinkFactory, InstallNodeFactory, ArchiveNodeFactory,
   CommandNodeFactory below). A target whose sources are other targets
   (Install, Tarfile, ...) gets its nodes from those targets' outputs in the
   same pass, since dependency order has resolved them already; then the
   target's depends() edges are wired onto its nodes.
2. **Command expansion** (_expand_node_commands()): expand each node's
   command template (env.<tool>.<command_var> plus ToolchainContext
   overrides) into node._build_info["command"].

Targets are just descriptions until resolve() is called, so output_name,
flags, etc. can be customized after target creation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.debug import is_enabled, trace, trace_value
from pcons.core.graph import topological_sort_targets
from pcons.core.node import FileNode, Node
from pcons.core.subst import PathToken, TargetPath, ToolPath

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


class PendingSourceFactory:
    """Base factory for targets whose sources may be other Targets.

    A "pending source" is a source given as a Target: it stands for that
    target's outputs. A source target is a dependency, and targets resolve
    in dependency order, so those outputs exist by the time
    ``resolve_pending`` creates this target's nodes from them.
    """

    def __init__(self, project: Project) -> None:
        self.project = project

    def resolve(
        self,
        target: Target,  # noqa: ARG002
        env: Environment | None,  # noqa: ARG002
    ) -> None:
        """No-op by default: the nodes come from resolve_pending()."""

    def resolve_pending(self, target: Target) -> None:
        """Create the target's nodes from its resolved sources.

        Subclasses must override.
        """
        raise NotImplementedError

    def _resolve_sources(self, target: Target) -> list[FileNode]:
        """Resolve pending sources to FileNodes.

        Handles Target sources (extracts output_nodes — final products
        only, not intermediates), FileNode passthrough, and Path/str
        sources (creates nodes via project).

        A written-out relative source is read from the directory of the
        script that declared the target, whose offset from the top-level
        root the target carries. Resolution runs long after that script
        finished, so the offset comes from the target rather than from the
        project's current directory.
        """
        from pcons.core.target import Target as TargetClass

        if target._pending_sources is None:
            return []

        offset = target._subdir
        resolved: list[FileNode] = []
        for source in target._pending_sources:
            if isinstance(source, TargetClass):
                resolved.extend(source.output_nodes)
            elif isinstance(source, FileNode):
                resolved.append(source)
            elif isinstance(source, Node):
                pass
            elif isinstance(source, (Path, str)):
                resolved.append(
                    self.project.node(offset / source if offset.parts else source)
                )
        return resolved


class NoOpFactory(PendingSourceFactory):
    """Factory for targets that need no resolution (e.g., interface targets)."""

    def resolve_pending(self, target: Target) -> None:
        """No-op: nothing to resolve."""


class CommandNodeFactory(PendingSourceFactory):
    """Factory for the targets ``env.Command`` declares.

    Command targets already have output_nodes from GenericCommandBuilder,
    so this factory has two jobs on the build_info those nodes carry:
    ``resolve`` turns whatever the command line names into paths, and
    ``resolve_pending`` wires up sources given as Targets.
    """

    def resolve(
        self,
        target: Target,
        env: Environment | None,  # noqa: ARG002
    ) -> None:
        """Replace each Target or Node written into the command with its path.

        ``env.Command(command=[tool, ...])`` names what runs the command, and
        the token survives declaration as the object itself. The resolver
        resolves a target's dependencies before dispatching here, and
        ``env.Command`` recorded the tool and every Target written into the
        list with ``depends()``, so the outputs those tokens stand for exist
        by the time this runs. Each becomes a ``PathToken`` the generators
        render the way they render every other path they produce.

        The dependency edge is not made here: ``env.Command`` already made it,
        which keeps the tool out of ``$SOURCES`` and leaves the caller's
        indices meaning what they meant.
        """
        from pcons.core.target import Target as TargetClass

        if not target.output_nodes:
            return
        build_info = target.output_nodes[0]._build_info or {}
        command = build_info.get("command")
        if not command or not any(
            isinstance(token, (TargetClass, FileNode, ToolPath)) for token in command
        ):
            return
        tool = (getattr(target, "_builder_data", None) or {}).get("tool")
        build_info["command"] = [
            _resolved_command_token(target, token, tool, program=index == 0)
            for index, token in enumerate(command)
        ]

    def resolve_pending(self, target: Target) -> None:
        """Add each source Target's outputs as dependencies of the command's
        output nodes and include them in _build_info's sources."""
        additional_sources = self._resolve_sources(target)

        if not additional_sources:
            return

        # Add as dependencies to command's output nodes
        for node in target.output_nodes:
            node.add_inputs(additional_sources)

        # Update _build_info to include additional sources
        if target.output_nodes:
            primary = target.output_nodes[0]
            if hasattr(primary, "_build_info") and primary._build_info:
                existing_sources = primary._build_info.get("sources", [])
                primary._build_info["sources"] = self._ordered_sources(
                    target, list(existing_sources), additional_sources
                )

    @staticmethod
    def _ordered_sources(
        target: Target,
        existing: list[FileNode],
        additional: list[FileNode],
    ) -> list[FileNode]:
        """Merge resolved Target outputs back into the declared source order.

        ``env.Command`` records the sequence the script wrote, with Targets
        left in place because their outputs don't exist yet. Substituting each
        Target with its outputs here keeps ``$SOURCE``, ``${SOURCES[n]}`` and
        the edge's input order agreeing with the call. Appending instead —
        which is what happens without a declared sequence — silently shifts
        every positional reference.
        """
        declared = (getattr(target, "_builder_data", None) or {}).get(
            "declared_sources"
        )
        if not declared:
            return existing + additional

        from pcons.core.target import Target as TargetClass

        ordered: list[FileNode] = []
        for entry in declared:
            if isinstance(entry, TargetClass):
                ordered.extend(entry.output_nodes)
            elif isinstance(entry, FileNode):
                ordered.append(entry)

        # Anything the declared sequence didn't account for (sources added
        # after the fact) follows, in the order it was added.
        # Keyed on the path, like every other node identity in pcons
        # (project._nodes, Target._source_set, Node.depends).
        seen = {node.path for node in ordered}
        ordered.extend(n for n in existing + additional if n.path not in seen)
        return ordered


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

        # Wire attached scanners (discovered dependencies): per-edge scan
        # nodes, per-target collate nodes, dyndep stamping. After the
        # toolchain hooks (which may attach scanners) and before command
        # expansion (so per-edge vars exist when templates expand).
        from pcons.core.scan import ScannerResolver

        ScannerResolver(self.project).run(self._targets_in_build_order())

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

        trace("resolve", "Resolving target: %s", target.name)

        env = target._env

        # Build order resolves a target's dependencies first: the targets it
        # depends on, the libraries it links and the targets whose outputs
        # are its sources. A target reached out of order (a caller resolving
        # one target directly) resolves them here, before its factory reads
        # them.
        for dep in target.dependencies:
            if not dep._resolved:
                self._resolve_target(dep)

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

        if target._pending_sources is not None:
            self._resolve_pending_sources(target)

        # The nodes exist and the dependencies are resolved: wire the
        # depends() edges, as loosely as each node allows.
        target._apply_dependencies()

        if target.output_nodes:
            trace(
                "resolve",
                "  Output: %s",
                [str(n.path) for n in target.output_nodes],
            )

        target._resolved = True

    def _resolve_pending_sources(self, target: Target) -> None:
        """Let the target's factory create its nodes from its Target sources,
        which have resolved by now."""
        builder_name = target._builder_name
        factory = (
            self._builder_factories.get(builder_name)
            if builder_name is not None
            else None
        )
        if factory is not None:
            factory.resolve_pending(target)
        elif target._pending_sources:
            logger.warning(
                "Target '%s' has pending sources but no factory registered for "
                "builder '%s'. Sources will not be resolved.",
                target.name,
                builder_name,
            )
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
        from pcons.core.subst import NodeVar, SourcePath

        extra_vars: dict[str, object] = {}
        extra_vars["SOURCE"] = SourcePath()
        extra_vars["SOURCES"] = SourcePath()  # Generator handles single vs. multiple
        extra_vars["TARGET"] = TargetPath()
        extra_vars["TARGETS"] = TargetPath()  # Generator handles single vs. multiple

        # Context overrides take precedence over tool_config values
        extra_vars.update(tool_overrides)

        # Per-node template variables (e.g. a grouped compile node's
        # MODULE_NAME, set by a toolchain's setup_group_node hook). The
        # reference stays in the command as a marker rather than expanding to
        # the value: a ninja rule is identified by its command text, so
        # substituting here would give every module or resource a rule of its
        # own. The generators read the value from build_info["vars"].
        node_vars = build_info.get("vars")
        if node_vars:
            extra_vars.update({name: NodeVar(name) for name in node_vars})

        # Tokens stay separate; the generator joins them with shell quoting
        command_tokens = env.subst_list(cmd_template, **extra_vars)
        build_info["command"] = command_tokens

        # Kept beside the command rather than in front of it: generators that
        # report the real compiler (compile_commands.json) leave it out.
        from pcons.core.launcher import resolve_launcher

        launcher = resolve_launcher(env, tool_name)
        if launcher:
            build_info["launcher"] = launcher
        trace(
            "subst",
            "  Expanded command: %s",
            command_tokens[:10] if len(command_tokens) > 10 else command_tokens,
        )


def _resolved_command_token(
    owner: Target, token: Any, tool: Any, *, program: bool = False
) -> Any:
    """One command token with whatever stands for a path turned into one.

    @param owner The Command target whose command line this token sits in.
    @param token The token as the script wrote it.
    @param tool What ``tool=`` named, if anything.
    @param program This token is the first of the line, so a Target or
        FileNode here is what runs and is spelled to run.
    @return The token, with anything standing for a path turned into a
        ``PathToken``.
    """
    from pcons.core.target import Target as TargetClass

    if isinstance(token, (TargetClass, FileNode)):
        path = _command_path(owner, token)
        return replace(path, executable=True) if program else path
    if isinstance(token, ToolPath):
        return _tool_token(owner, tool, token)
    return token


def _tool_token(owner: Target, tool: Any, marker: ToolPath) -> Any:
    """What ``$TOOL`` stands for, spelled so the shell will run it.

    A tool this build produces is a path, and the only one whose spelling
    pcons owns. Anything else is the caller's own word for a program that
    already exists — an absolute path to an installed tool, or a bare name
    for ``$PATH`` — and passes through, since rewriting either would only
    break it.
    """
    from pcons.core.target import Target as TargetClass

    if isinstance(tool, TargetClass):
        path = _command_path(owner, tool)
        return replace(
            path, prefix=marker.prefix, suffix=marker.suffix, executable=True
        )
    text = str(tool)
    if os.path.isabs(text):
        return PathToken(
            prefix=marker.prefix,
            path=text,
            path_type="absolute",
            suffix=marker.suffix,
            executable=True,
        )
    return f"{marker.prefix}{text}{marker.suffix}"


def _command_path(owner: Target, token: Target | FileNode) -> PathToken:
    """The path a Target or FileNode written into *owner*'s command stands for.

    A target has to name one file for this to mean anything, so one that
    builds several says which of them it meant rather than having a choice
    made for it.
    """
    from pcons.core.errors import PconsError
    from pcons.core.target import Target as TargetClass

    if not isinstance(token, TargetClass):
        return PathToken(path=str(token.path), path_type="project")

    outputs = token.output_nodes
    if not outputs:
        raise PconsError(
            f"command for '{owner.qualified_name}' names target "
            f"'{token.qualified_name}', which builds no file.",
            location=owner.defined_at,
        )
    if len(outputs) > 1:
        names = ", ".join(str(n.path) for n in outputs)
        raise PconsError(
            f"command for '{owner.qualified_name}' names target "
            f"'{token.qualified_name}', which builds several files "
            f"({names}), so which one to run is not decided. Write the "
            f"one that is meant: {token.name}.output_nodes[0].",
            location=owner.defined_at,
        )
    return PathToken(path=str(outputs[0].path), path_type="project")
