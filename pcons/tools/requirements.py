# SPDX-License-Identifier: MIT
"""Effective requirements: a target's merged compile/link settings.

Merges base environment settings, the target's private requirements, and
all dependencies' public requirements (transitive).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.flags import get_separated_arg_flags_from_toolchains, merge_flags

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pcons.core.environment import Environment
    from pcons.core.subst import PathToken
    from pcons.core.target import Target, UsageRequirements

logger = logging.getLogger(__name__)


@dataclass
class EffectiveRequirements:
    """Complete compilation/link requirements for a target.

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
    compile_flags: list[str | PathToken] = field(default_factory=list)
    link_flags: list[str | PathToken] = field(default_factory=list)
    link_libs: list[str | Target] = field(default_factory=list)
    link_dirs: list[Path] = field(default_factory=list)
    separated_arg_flags: frozenset[str] = field(default_factory=frozenset)

    def merge(self, reqs: UsageRequirements) -> None:
        """Merge in UsageRequirements: order-preserving dedup, with
        separated-arg flag pairs (``-framework Foo``) treated as units.
        """
        for inc_dir in reqs.include_dirs:
            inc_path = Path(inc_dir) if isinstance(inc_dir, str) else inc_dir
            if inc_path not in self.includes:
                self.includes.append(inc_path)
        for define in reqs.defines:
            if define not in self.defines:
                self.defines.append(define)
        merge_flags(self.compile_flags, reqs.compile_flags, self.separated_arg_flags)
        merge_flags(self.link_flags, reqs.link_flags, self.separated_arg_flags)
        for lib in reqs.link_libs:
            if lib not in self.link_libs:
                self.link_libs.append(lib)
        for lib_dir in reqs.link_dirs:
            dir_path = Path(lib_dir) if isinstance(lib_dir, str) else lib_dir
            if dir_path not in self.link_dirs:
                self.link_dirs.append(dir_path)

    def as_hashable_tuple(self) -> tuple:
        """Return a hashable representation for caching."""
        return (
            tuple(str(p) for p in self.includes),
            tuple(self.defines),
            tuple(self.compile_flags),
            tuple(self.link_flags),
            tuple(self.link_libs),
            tuple(str(p) for p in self.link_dirs),
        )

    def clone(self) -> EffectiveRequirements:
        """Create a deep copy with copied lists."""
        return EffectiveRequirements(
            includes=list(self.includes),
            defines=list(self.defines),
            compile_flags=list(self.compile_flags),
            link_flags=list(self.link_flags),
            link_libs=list(self.link_libs),
            link_dirs=list(self.link_dirs),
            separated_arg_flags=self.separated_arg_flags,
        )


def apply_requirements_to_env(env: Environment, reqs: UsageRequirements) -> None:
    """Apply usage requirements env-wide onto tool variables (``env.use()``).

    Compile requirements land on cc/cxx; link requirements on link, with
    the same merge semantics as :meth:`EffectiveRequirements.merge`. A
    ``Target`` in ``link_libs`` is an error here: linking a build target
    is per-target dependency information — use ``target.link(...)``.
    """
    from pcons.core.target import Target as _Target

    sep = get_separated_arg_flags_from_toolchains(env.toolchains)
    eff = EffectiveRequirements(separated_arg_flags=sep)
    eff.merge(reqs)

    for lib in eff.link_libs:
        if isinstance(lib, _Target):
            raise ValueError(
                f"env.use() got a Target ('{lib.name}') in link_libs; "
                f"linking a build target is per-target information — use "
                f"target.link({lib.name!r}) instead."
            )

    def var(tool: Any, name: str) -> list[Any]:
        """The tool's list variable, created empty on first need."""
        if not hasattr(tool, name):
            setattr(tool, name, [])
        return getattr(tool, name)

    def extend_unique(tool: Any, name: str, values: Iterable[Any]) -> None:
        """Append each absent value; with nothing to add, don't create the var."""
        items = list(values)
        if not items:
            return
        dest = var(tool, name)
        for value in items:
            if value not in dest:
                dest.append(value)

    for tool_name in ("cc", "cxx"):
        if not env.has_tool(tool_name):
            continue
        tool = getattr(env, tool_name)
        extend_unique(tool, "includes", (str(inc) for inc in eff.includes))
        extend_unique(tool, "defines", eff.defines)
        if eff.compile_flags:
            merge_flags(var(tool, "flags"), eff.compile_flags, sep)

    if env.has_tool("link"):
        link = env.link
        extend_unique(link, "libdirs", (str(d) for d in eff.link_dirs))
        extend_unique(link, "libs", eff.link_libs)
        if eff.link_flags:
            merge_flags(var(link, "flags"), eff.link_flags, sep)
        # Structured frameworks (macOS) — same variables env.Framework() uses.
        extend_unique(link, "frameworks", reqs.frameworks)
        extend_unique(link, "frameworkdirs", (str(d) for d in reqs.framework_dirs))


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

    Layers (in order of application): base environment, the target's
    private then public requirements, dependencies' public requirements
    (transitive), then implicit target deps.

    Args:
        target: The target to compute requirements for.
        env: The environment providing base configuration.
        for_compilation: If True, compute for compilation phase.
                        If False, compute for linking phase.

    Returns:
        EffectiveRequirements containing the merged requirements.
    """
    separated_arg_flags = get_separated_arg_flags_from_toolchains(env.toolchains)
    result = EffectiveRequirements(separated_arg_flags=separated_arg_flags)

    # Layer 1: Base environment (primary tool's includes and defines).
    tool_name = _get_primary_tool(target, env)
    if tool_name and env.has_tool(tool_name):
        tool_config = getattr(env, tool_name)

        includes = getattr(tool_config, "includes", None)
        if includes:
            for inc in includes:
                path = Path(inc) if isinstance(inc, str) else inc
                if path not in result.includes:
                    result.includes.append(path)

        defines = getattr(tool_config, "defines", None)
        if defines:
            for define in defines:
                if define not in result.defines:
                    result.defines.append(define)

        # NOT env.<tool>.flags: in mixed-language targets the primary
        # tool's flags (e.g. cxx.flags with -std=c++20) would leak to all
        # sources. Per-tool base flags are applied per-source in
        # resolver.py. env.link settings are likewise baked into the
        # ninja rule via template expansion, not merged here.

    # Layer 2: Target's own requirements. Public is also available to the
    # target's own sources, not just consumers.
    result.merge(_resolve_and_add_includes_for(target.private, target))
    result.merge(_resolve_and_add_includes_for(target.public, target))

    # Layer 3: All dependencies' public requirements (transitive).
    for dep in target.transitive_dependencies():
        result.merge(_resolve_and_add_includes_for(dep.public, dep))

    # Layer 4: Implicit target deps from target.depends(other_target):
    # propagate public usage requirements to compile steps without adding
    # outputs to the linker's $in.
    if for_compilation:
        for dep in target._implicit_target_deps:
            result.merge(_resolve_and_add_includes_for(dep.public, dep))

    return result


def _get_primary_tool(target: Target, env: Environment) -> str | None:
    """Determine the primary compilation tool for a target.

    Checks required_languages, then asks each toolchain for a source
    handler, then falls back to cxx/cc.

    Returns:
        Tool name (e.g., 'cc', 'cxx') or None if not determinable.
    """
    if "cxx" in target.required_languages:
        return "cxx"
    if "c" in target.required_languages:
        return "cc"

    from pcons.core.node import FileNode

    for source in target.sources:
        if isinstance(source, FileNode):
            for toolchain in env.toolchains:
                handler = toolchain.get_source_handler(source.path.suffix)
                if handler is not None:
                    return handler.tool_name

    if env.has_tool("cxx"):
        return "cxx"
    if env.has_tool("cc"):
        return "cc"

    return None
