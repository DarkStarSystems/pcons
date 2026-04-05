# SPDX-License-Identifier: MIT
"""Compile-link factory for building programs and libraries.

This module provides the CompileLinkFactory, which handles the resolution
of compile-then-link targets (Program, StaticLibrary, SharedLibrary, Object).
It implements the NodeFactory protocol from the builder registry.

This logic was extracted from pcons/core/resolver.py to keep the core
tool-agnostic. The resolver dispatches to this factory via the builder
registry, just like it dispatches to InstallNodeFactory or ArchiveNodeFactory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.debug import is_enabled, trace, trace_value
from pcons.core.node import FileNode
from pcons.core.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)
from pcons.core.subst import PathToken, TargetPath
from pcons.core.target import TargetType
from pcons.toolchains.build_context import CompileLinkContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


class CompileLinkFactory:
    """Factory for compile-then-link targets (Program, Library, etc.).

    Implements the NodeFactory protocol. Handles:
    - Creating object nodes for each source file (compilation step)
    - Creating output nodes (libraries, programs) from objects (link step)
    - Object caching across targets with identical source + requirements
    - Source handler dispatch (tool-agnostic: delegates to toolchain)
    - Auxiliary input handling (.def files, etc.)
    - Language detection for linker selection

    This class combines the logic that was previously in ObjectNodeFactory
    and OutputNodeFactory in the resolver.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self._object_cache: dict[tuple[Path, tuple], FileNode] = {}
        # Maps language -> list of (source_path, obj_node) pairs.
        # Populated by _create_object_node(); passed to toolchain after_resolve() hooks.
        self._source_obj_by_language: dict[str, list[tuple[Path, FileNode]]] = {}

    # -------------------------------------------------------------------------
    # NodeFactory protocol
    # -------------------------------------------------------------------------

    def resolve(self, target: Target, env: Environment | None) -> None:
        """Resolve a compile-link target.

        Steps:
        1. Compute effective requirements
        2. Create object nodes for each source (compilation)
        3. Create output node (library/program) from objects (linking)
        """
        if env is None:
            if target.target_type == TargetType.INTERFACE:
                return
            logger.debug("Skipping target '%s' without env", target.name)
            return

        trace("resolve", "Resolving target: %s", target.name)

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
            for flag in target_type_flags:
                if flag not in effective.compile_flags:
                    effective.compile_flags.append(flag)

        # Determine the language for this target based on sources
        language = self._determine_language(target, env)
        if language:
            target.required_languages.add(language)

        # Separate sources into compilable sources and auxiliary inputs
        auxiliary_inputs: list[tuple[FileNode, str, AuxiliaryInputHandler]] = []

        # Create object nodes for each source (delegated to helper methods)
        trace("resolve", "  Creating object nodes for %d sources", len(target.sources))
        for source in target.sources:
            if isinstance(source, FileNode):
                # Check if this is an auxiliary input file
                aux_handler = self._get_auxiliary_input_handler(source.path, env)
                if aux_handler is not None:
                    flag = aux_handler.flag_template.replace("$file", str(source.path))
                    auxiliary_inputs.append((source, flag, aux_handler))
                    trace("resolve", "    %s -> auxiliary input", source.path)
                    continue

                # Normal source file - create object node
                obj_node = self._create_object_node(target, source, effective, env)
                if obj_node:
                    target.intermediate_nodes.append(obj_node)
                    trace("resolve", "    %s -> %s", source.path, obj_node.path)

        # Store auxiliary inputs on the target for use by output creation
        if auxiliary_inputs:
            target._builder_data["auxiliary_inputs"] = auxiliary_inputs

        # Create output node(s) based on target type
        trace("resolve", "  Creating output for type: %s", target.target_type)
        if target.target_type == TargetType.STATIC_LIBRARY:
            self._create_static_library_output(target, env)
        elif target.target_type == TargetType.SHARED_LIBRARY:
            self._create_shared_library_output(target, env)
        elif target.target_type == TargetType.PROGRAM:
            self._create_program_output(target, env)
        elif target.target_type == TargetType.OBJECT:
            # Object-only targets: output_nodes are the object files
            target.output_nodes = list(target.intermediate_nodes)
            target.nodes = list(target.intermediate_nodes)

        if target.output_nodes:
            trace("resolve", "  Output: %s", [str(n.path) for n in target.output_nodes])

    def resolve_pending(self, target: Target) -> None:
        """No-op: compile-link targets don't have pending sources."""

    # -------------------------------------------------------------------------
    # Object node creation (compilation step)
    # -------------------------------------------------------------------------

    def _get_source_handler(
        self, source: Path, env: Environment
    ) -> SourceHandler | None:
        """Get source handler from any of the environment's toolchains."""
        for toolchain in env.toolchains:
            handler = toolchain.get_source_handler(source.suffix)
            if handler is not None:
                if env.has_tool(handler.tool_name):
                    return handler
                else:
                    logger.warning(
                        "Tool '%s' required for '%s' files is not available in the "
                        "environment. Configure the toolchain or add the tool manually.",
                        handler.tool_name,
                        source.suffix,
                    )
        return None

    def _get_auxiliary_input_handler(
        self, source: Path, env: Environment
    ) -> AuxiliaryInputHandler | None:
        """Get auxiliary input handler from any of the environment's toolchains."""
        for toolchain in env.toolchains:
            handler = toolchain.get_auxiliary_input_handler(source.suffix)
            if handler is not None:
                return handler
        return None

    def _get_object_path(self, target: Target, source: Path, env: Environment) -> Path:
        """Generate target-specific output path for an object file.

        Format: ``<build_dir>/obj.<target>/<relative_dir>/<name>.<src_ext><obj_ext>``
        """
        build_dir = self.project.build_dir
        obj_dir = build_dir / f"obj.{target.name}"

        handler = self._get_source_handler(source, env)
        if handler:
            obj_suffix = handler.object_suffix
        else:
            toolchain = env._toolchain
            obj_suffix = toolchain.get_object_suffix() if toolchain else ".o"

        obj_name = source.name + obj_suffix
        rel_dir = source.parent
        parts = [
            p for p in rel_dir.parts if p not in ("..", "/") and p != rel_dir.anchor
        ]
        if parts:
            return obj_dir.joinpath(*parts) / obj_name
        return obj_dir / obj_name

    def _resolve_depfile(
        self, depfile_spec: TargetPath | None, target_path: Path
    ) -> PathToken | None:
        """Resolve depfile specification to a concrete PathToken."""
        if depfile_spec is None:
            return None
        return PathToken(
            prefix=depfile_spec.prefix,
            path=str(target_path),
            path_type="build",
            suffix=depfile_spec.suffix,
        )

    def _create_object_node(
        self,
        target: Target,
        source: FileNode,
        effective: EffectiveRequirements,
        env: Environment,
    ) -> FileNode | None:
        """Create object file node with effective requirements in build_info.

        Implements object caching: if the same source is compiled with the
        same effective requirements, the same object node is reused.
        """
        handler = self._get_source_handler(source.path, env)
        if handler is None:
            return source

        tool_name = handler.tool_name
        language = handler.language
        deps_style = handler.deps_style
        command_var = handler.command_var

        effective_hash = effective.as_hashable_tuple()
        cache_key = (source.path.resolve(), effective_hash)

        if cache_key in self._object_cache:
            return self._object_cache[cache_key]

        obj_path = self._get_object_path(target, source.path, env)
        obj_node = self.project.node(obj_path)
        obj_node.depends([source])

        depfile = self._resolve_depfile(handler.depfile, obj_path)

        if source.explicit_deps:
            obj_node.implicit_deps.extend(source.explicit_deps)

        context = CompileLinkContext.from_effective_requirements(
            effective,
            mode="compile",
            tool_name=tool_name,
            env=env,
        )

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

        self._object_cache[cache_key] = obj_node

        self._source_obj_by_language.setdefault(language, []).append(
            (source.path, obj_node)
        )

        env.register_node(obj_node)
        return obj_node

    # -------------------------------------------------------------------------
    # Output node creation (link step)
    # -------------------------------------------------------------------------

    def _create_static_library_output(self, target: Target, env: Environment) -> None:
        """Create static library output node."""
        if not target.intermediate_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir
        path_resolver = self.project.path_resolver

        if target.output_name:
            lib_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_static_library_name(target.name)
            lib_path = build_dir / lib_name
        else:
            lib_name = f"lib{target.name}.a"
            lib_path = build_dir / lib_name

        lib_node = self.project.node(lib_path)
        lib_node.depends(target.intermediate_nodes)

        effective_link = compute_effective_requirements(
            target, env, for_compilation=False
        )

        context = CompileLinkContext.from_effective_requirements(
            effective_link,
            mode="link",
        )

        archiver_tool = "ar"
        if toolchain := env._toolchain:
            archiver_tool = toolchain.get_archiver_tool_name()

        lib_node._build_info = {
            "tool": archiver_tool,
            "command_var": "libcmd",
            "sources": target.intermediate_nodes,
            "context": context,
            "env": env,
        }

        target.output_nodes.append(lib_node)
        target.nodes.append(lib_node)
        env.register_node(lib_node)

    def _create_shared_library_output(self, target: Target, env: Environment) -> None:
        """Create shared library output node."""
        if not target.intermediate_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir
        path_resolver = self.project.path_resolver

        if target.output_name:
            lib_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            lib_name = toolchain.get_shared_library_name(target.name)
            lib_path = build_dir / lib_name
        else:
            import sys

            if sys.platform == "darwin":
                lib_name = f"lib{target.name}.dylib"
            elif sys.platform == "win32":
                lib_name = f"{target.name}.dll"
            else:
                lib_name = f"lib{target.name}.so"
            lib_path = build_dir / lib_name

        lib_node = self.project.node(lib_path)
        lib_node.depends(target.intermediate_nodes)

        link_language, context = self._setup_link_node(target, env, lib_node)

        lib_node._build_info = {
            "tool": "link",
            "command_var": "sharedcmd",
            "language": link_language,
            "sources": target.intermediate_nodes,
            "context": context,
            "env": env,
        }

        import sys

        if sys.platform == "win32":
            import_lib_path = lib_path.with_suffix(".lib")
            lib_node._build_info["outputs"] = {
                "primary": {"path": lib_path, "suffix": lib_path.suffix},
                "import_lib": {"path": import_lib_path, "suffix": ".lib"},
            }

        target.output_nodes.append(lib_node)
        target.nodes.append(lib_node)
        env.register_node(lib_node)

    def _create_program_output(self, target: Target, env: Environment) -> None:
        """Create program output node."""
        if not target.intermediate_nodes:
            logger.warning(
                "Target '%s' has no sources - no output will be generated",
                target.name,
            )
            return

        build_dir = self.project.build_dir
        path_resolver = self.project.path_resolver

        if target.output_name:
            prog_path = build_dir / path_resolver.normalize_target_path(
                target.output_name
            )
        elif toolchain := env._toolchain:
            prog_name = toolchain.get_program_name(target.name)
            prog_path = build_dir / prog_name
        else:
            import sys

            if sys.platform == "win32":
                prog_name = f"{target.name}.exe"
            else:
                prog_name = target.name
            prog_path = build_dir / prog_name

        prog_node = self.project.node(prog_path)
        prog_node.depends(target.intermediate_nodes)

        link_language, context = self._setup_link_node(target, env, prog_node)

        prog_node._build_info = {
            "tool": "link",
            "command_var": "progcmd",
            "language": link_language,
            "sources": target.intermediate_nodes,
            "context": context,
            "env": env,
        }

        # Generic multi-output support for Program builders.
        from pcons.core.builder import MultiOutputBuilder
        from pcons.core.node import OutputInfo

        toolchain = env._toolchain
        if toolchain and "link" in toolchain.tools:
            link_tool = toolchain.tools["link"]
            program_builder = link_tool.builders().get("Program")
            if (
                isinstance(program_builder, MultiOutputBuilder)
                and len(program_builder.outputs) > 1
            ):
                outputs_dict: dict[str, OutputInfo] = {
                    "primary": OutputInfo(path=prog_path, suffix=prog_path.suffix),
                }
                for spec in program_builder.outputs[1:]:
                    secondary_path = prog_path.with_suffix(spec.suffix)
                    outputs_dict[spec.name] = OutputInfo(
                        path=secondary_path,
                        suffix=spec.suffix,
                        implicit=spec.implicit,
                    )
                    sec_node = self.project.node(secondary_path)
                    sec_node._build_info = {
                        "primary_node": prog_node,
                        "output_name": spec.name,
                    }
                    target.output_nodes.append(sec_node)
                    target.nodes.append(sec_node)
                prog_node._build_info["outputs"] = outputs_dict

        target.output_nodes.append(prog_node)
        target.nodes.append(prog_node)
        env.register_node(prog_node)

    # -------------------------------------------------------------------------
    # Link helpers
    # -------------------------------------------------------------------------

    def _setup_link_node(
        self,
        target: Target,
        env: Environment,
        output_node: FileNode,
    ) -> tuple[str, CompileLinkContext]:
        """Set up dependencies, auxiliary inputs, and link context for an output node.

        Shared logic for both shared library and program output creation.
        """
        builder_data = getattr(target, "_builder_data", {}) or {}
        auxiliary_inputs = builder_data.get("auxiliary_inputs", [])
        auxiliary_input_paths = {node.path for node, _, _ in auxiliary_inputs}

        dep_libs = self._collect_dependency_outputs(target)
        if dep_libs:
            dep_libs = [d for d in dep_libs if d.path not in auxiliary_input_paths]
            if dep_libs:
                output_node.depends(dep_libs)

        if auxiliary_inputs:
            linker_input_nodes = [node for node, _, _ in auxiliary_inputs]
            output_node.implicit_deps.extend(linker_input_nodes)

        effective_link = compute_effective_requirements(
            target, env, for_compilation=False
        )

        link_flags = list(effective_link.link_flags)
        seen_handlers: set[str] = set()
        for _, flag, handler in auxiliary_inputs:
            link_flags.append(flag)
            if handler.extra_flags and handler.suffix not in seen_handlers:
                link_flags.extend(handler.extra_flags)
                seen_handlers.add(handler.suffix)

        object_languages: set[str] = set()
        for node in target.intermediate_nodes:
            bi = getattr(node, "_build_info", None)
            if bi:
                lang = bi.get("language")
                if lang:
                    object_languages.add(lang)

        langs = target.get_all_languages() | object_languages
        primary_tc = env._toolchain
        priority = getattr(primary_tc, "language_priority", {}) if primary_tc else {}
        link_language = (
            max(langs, key=lambda lang: priority.get(lang, 0)) if langs else "c"
        )

        for tc in env.toolchains:
            runtime_libs = tc.get_runtime_libs(link_language, object_languages)
            if runtime_libs:
                effective_link.link_libs = effective_link.link_libs + runtime_libs
            runtime_libdirs = tc.get_runtime_libdirs(link_language, object_languages)
            if runtime_libdirs:
                effective_link.link_dirs = effective_link.link_dirs + [
                    Path(d) for d in runtime_libdirs
                ]

        effective_link.link_flags = link_flags
        context = CompileLinkContext.from_effective_requirements(
            effective_link,
            mode="link",
            language=link_language,
            env=env,
            target=target,
            output_name=output_node.path.name,
        )

        return link_language, context

    def _collect_dependency_outputs(self, target: Target) -> list[FileNode]:
        """Collect output nodes from all dependencies.

        For SharedLibrary dependencies on Windows, returns the import library
        (.lib) instead of the DLL (.dll) since that's what the linker needs.
        """
        import sys

        result: list[FileNode] = []
        for dep in target.transitive_dependencies():
            for node in dep.output_nodes:
                if (
                    sys.platform == "win32"
                    and dep.target_type == TargetType.SHARED_LIBRARY
                ):
                    build_info = getattr(node, "_build_info", {})
                    outputs = build_info.get("outputs", {})
                    import_lib_info = outputs.get("import_lib")
                    if import_lib_info and "path" in import_lib_info:
                        import_lib_path = import_lib_info["path"]
                        result.append(self.project.node(import_lib_path))
                        continue
                result.append(node)
        return result

    # -------------------------------------------------------------------------
    # Language detection
    # -------------------------------------------------------------------------

    def _determine_language(self, target: Target, env: Environment) -> str | None:
        """Determine the primary language for a target based on its sources.

        Uses toolchains to determine language in a tool-agnostic way.
        """
        languages: set[str] = set()

        for source in target.sources:
            if isinstance(source, FileNode):
                for toolchain in env.toolchains:
                    handler = toolchain.get_source_handler(source.path.suffix)
                    if handler:
                        languages.add(handler.language)
                        break

        if not languages:
            return None

        primary_toolchain = env._toolchain
        if primary_toolchain is None:
            return next(iter(languages))

        priority = getattr(primary_toolchain, "language_priority", {})
        max_priority = -1
        max_lang: str | None = None

        for lang in languages:
            p = priority.get(lang, 0)
            if p > max_priority:
                max_priority = p
                max_lang = lang

        return max_lang
