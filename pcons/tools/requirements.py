# SPDX-License-Identifier: MIT
"""Effective requirements computation for target-centric builds.

This module provides the EffectiveRequirements class that holds the complete
compilation/link requirements for a target, computed by merging:
1. Base environment settings
2. Target's private requirements
3. All dependencies' public requirements (transitive)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.flags import get_separated_arg_flags_from_toolchains, merge_flags
from pcons.core.target import Target

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.target import UsageRequirements

logger = logging.getLogger(__name__)


@dataclass
class EffectiveRequirements:
    """Complete compilation/link requirements for a target.

    This represents the final, resolved set of flags and paths that should
    be used when compiling sources for a specific target. It combines:
    - Base environment settings (env.cc.includes, etc.)
    - Target's private requirements
    - All dependencies' public requirements (transitive)

    Attributes:
        includes: Include directories for compilation.
        defines: Preprocessor definitions.
        compile_flags: Additional compiler flags.
        link_flags: Linker flags.
        link_libs: Libraries to link against.
        link_dirs: Library search directories.
        separated_arg_flags: Set of flags that take separate arguments,
                            used for proper flag deduplication.
    """

    includes: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    compile_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    link_libs: list[str | Target] = field(default_factory=list)
    link_dirs: list[Path] = field(default_factory=list)
    separated_arg_flags: frozenset[str] = field(default_factory=frozenset)

    def merge(self, reqs: UsageRequirements) -> None:
        """Merge UsageRequirements into this EffectiveRequirements.

        Avoids duplicates while preserving order. For compiler and linker
        flags, handles flags that take separate arguments (like -F path,
        -framework Foo) by treating the flag+argument pair as a unit.

        Args:
            reqs: UsageRequirements to merge in.
        """
        for inc_dir in reqs.include_dirs:
            inc_path = Path(inc_dir) if isinstance(inc_dir, str) else inc_dir
            if inc_path not in self.includes:
                self.includes.append(inc_path)
        for define in reqs.defines:
            if define not in self.defines:
                self.defines.append(define)
        # Use flag-aware merge for compile and link flags
        merge_flags(
            self.compile_flags,
            [str(f) for f in reqs.compile_flags],
            self.separated_arg_flags,
        )
        merge_flags(
            self.link_flags, [str(f) for f in reqs.link_flags], self.separated_arg_flags
        )
        for lib in reqs.link_libs:
            if lib not in self.link_libs:
                self.link_libs.append(str(lib) if not isinstance(lib, Target) else lib)
        for lib_dir in reqs.link_dirs:
            dir_path = Path(lib_dir) if isinstance(lib_dir, str) else lib_dir
            if dir_path not in self.link_dirs:
                self.link_dirs.append(dir_path)

    def as_hashable_tuple(self) -> tuple:
        """Return hashable representation for caching.

        This can be used as a dictionary key or set member to identify
        unique compilation configurations.

        Returns:
            A tuple containing all requirements in a hashable form.
        """
        return (
            tuple(str(p) for p in self.includes),
            tuple(self.defines),
            tuple(self.compile_flags),
            tuple(self.link_flags),
            tuple(self.link_libs),
            tuple(str(p) for p in self.link_dirs),
        )

    def clone(self) -> EffectiveRequirements:
        """Create a deep copy of this EffectiveRequirements.

        Returns:
            A new EffectiveRequirements with copied lists.
        """
        return EffectiveRequirements(
            includes=list(self.includes),
            defines=list(self.defines),
            compile_flags=list(self.compile_flags),
            link_flags=list(self.link_flags),
            link_libs=list(self.link_libs),
            link_dirs=list(self.link_dirs),
            separated_arg_flags=self.separated_arg_flags,
        )


def _resolve_and_add_includes_for(
    reqs: UsageRequirements, owner: Target
) -> UsageRequirements:
    """Resolve include directories in requirements and return a new UsageRequirements."""
    result = reqs.clone()

    def _update_include(inc: str | Path) -> Path:
        p = Path(inc) if not isinstance(inc, Path) else inc
        if owner._subdir and not p.is_absolute():
            p = Path(owner._subdir) / p
        # Canonicalize relative to the top-level project's resolver so
        # generators (which use the top-level resolver) see consistent
        # project-relative paths for includes coming from subprojects.
        top = owner.project.top_level()
        return top.path_resolver.canonicalize(p)

    result.include_dirs = [_update_include(inc) for inc in reqs.include_dirs]
    return result


def compute_effective_requirements(
    target: Target,
    env: Environment,
    for_compilation: bool = True,
) -> EffectiveRequirements:
    """Compute complete requirements for a target.

    Layers (in order of application):
    1. Base environment (env.cc.includes, env.cc.defines, etc.)
    2. Target's private requirements
    3. All dependencies' public requirements (transitive)

    Args:
        target: The target to compute requirements for.
        env: The environment providing base configuration.
        for_compilation: If True, compute for compilation phase.
                        If False, compute for linking phase.

    Returns:
        EffectiveRequirements containing the merged requirements.
    """
    # Get separated arg flags from toolchains for proper flag deduplication
    separated_arg_flags = get_separated_arg_flags_from_toolchains(env.toolchains)
    result = EffectiveRequirements(separated_arg_flags=separated_arg_flags)

    # Layer 1: Base environment
    # Try to get tool config for the primary language
    tool_name = _get_primary_tool(target, env)
    if tool_name and env.has_tool(tool_name):
        tool_config = getattr(env, tool_name)

        # Get includes from tool config
        includes = getattr(tool_config, "includes", None)
        if includes:
            for inc in includes:
                path = Path(inc) if isinstance(inc, str) else inc
                if path not in result.includes:
                    result.includes.append(path)

        # Get defines from tool config
        defines = getattr(tool_config, "defines", None)
        if defines:
            for define in defines:
                if define not in result.defines:
                    result.defines.append(define)

        # NOTE: We intentionally do NOT merge env.<tool>.flags here.
        # In mixed-language targets (C + C++), the primary tool's flags
        # (e.g., cxx.flags with -std=c++20) would leak to all sources.
        # Instead, per-tool base flags are applied in resolver.py's
        # _expand_single_node_command(), where tool_name is per-source.

    # Note: env.link settings (frameworks, libs, libdirs) are baked into the
    # ninja rule via template expansion. Effective requirements only contain
    # target-specific settings (private/public requirements from targets and
    # their dependencies).

    # Layer 2: Target's own requirements
    # Private: only for this target
    result.merge(_resolve_and_add_includes_for(target.private, target))
    # Public: also available to this target's own sources (not just consumers)
    result.merge(_resolve_and_add_includes_for(target.public, target))

    # Layer 3: All dependencies' public requirements (transitive)
    # transitive_dependencies() includes direct dependencies via DFS
    for dep in target.transitive_dependencies():
        result.merge(_resolve_and_add_includes_for(dep.public, dep))

    # Layer 4: Implicit target deps from target.depends(other_target)
    # These propagate public usage requirements (includes, defines) to
    # compile steps, just like link() deps, but without adding outputs
    # to the linker's $in.  Only propagated deps (not output-only).
    if for_compilation:
        for dep in target._implicit_target_deps:
            result.merge(_resolve_and_add_includes_for(dep.public, dep))

    return result


def _get_primary_tool(target: Target, env: Environment) -> str | None:
    """Determine the primary compilation tool for a target.

    Uses all toolchains to determine the tool (tool-agnostic), falling back
    to hardcoded suffixes if no toolchain handles the source.

    Args:
        target: The target to analyze.
        env: The environment to check for available tools.

    Returns:
        Tool name (e.g., 'cc', 'cxx') or None if not determinable.
    """
    # Check required_languages first
    if "cxx" in target.required_languages:
        return "cxx"
    if "c" in target.required_languages:
        return "cc"

    # Try toolchain-based detection (tool-agnostic approach)
    from pcons.core.node import FileNode

    for source in target.sources:
        if isinstance(source, FileNode):
            # Check all toolchains in order
            for toolchain in env.toolchains:
                handler = toolchain.get_source_handler(source.path.suffix)
                if handler is not None:
                    return handler.tool_name

    # Default to C++ if available
    if env.has_tool("cxx"):
        return "cxx"
    if env.has_tool("cc"):
        return "cc"

    return None
