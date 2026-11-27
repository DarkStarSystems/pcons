# SPDX-License-Identifier: MIT
"""Precompiled headers for C and C++ targets: the precompile_headers option.

A target lists headers. For each of C and C++ that its sources use, pcons
writes a wrapper header including them, compiles it once with exactly the
flags those sources compile with, and has each of them compile with it. How a
compiler makes and uses one is the toolchain's
(:meth:`~pcons.tools.toolchain.BaseToolchain.precompiled_header`); this module
plans the build steps.

Another target may reuse one, with the option set to that target: its sources
must then compile with the same defines and flags, which is checked here
rather than left to the compiler to refuse halfway through a build.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.collate import write_text_if_changed
from pcons.core.errors import PconsTypeError, PconsValueError
from pcons.core.node import FileNode
from pcons.core.target import Target
from pcons.core.vars import get_var
from pcons.toolchains.build_context import CompileLinkContext

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.subst import PathToken
    from pcons.tools.compile_link import CompileLinkFactory
    from pcons.tools.requirements import EffectiveRequirements
    from pcons.tools.toolchain import SourceHandler, Toolchain

OPTION = "precompile_headers"
#: A build variable that turns precompiled headers off, for a build that
#: checks every source includes what it uses.
ENABLE_VAR = "PCONS_PRECOMPILE_HEADERS"

# Language -> (name in messages, wrapper header suffix, wrapper source suffix)
_LANGUAGES = {"c": ("C", ".h", ".c"), "cxx": ("C++", ".hxx", ".cxx")}


@dataclass
class PrecompiledHeaderBuild:
    """One target's precompiled header for one language."""

    owner: Target
    language: str
    toolchain: Toolchain
    #: What each compile that uses it depends on.
    pch: FileNode
    #: The step that makes it: the precompiled header itself, or the object
    #: of the source compiled to make it.
    compile_node: FileNode
    #: That object, when it must be linked by every target using it (MSVC).
    link_object: FileNode | None
    use_flags: list[str | PathToken]
    database_flags: list[str]
    #: How its sources compile, to check a target reusing it against.
    settings: dict[str, list[str]]


class PrecompiledHeaders:
    """Plans the precompiled headers of a CompileLinkFactory's targets.

    Each is made once, by whichever target needs it first: a target reusing
    another's may resolve before it.
    """

    def __init__(self, factory: CompileLinkFactory) -> None:
        self._factory = factory
        self._builds: dict[tuple[int, str], PrecompiledHeaderBuild | None] = {}
        self._checked: set[tuple[int, str]] = set()

    def for_source(
        self,
        target: Target,
        found: tuple[SourceHandler, Toolchain],
        effective: EffectiveRequirements,
        env: Environment,
    ) -> PrecompiledHeaderBuild | None:
        """The precompiled header a source of *target* compiles with, if any.

        *found* is the source's handler and toolchain, *effective* its
        requirements and *env* its environment, the target's own.
        """
        handler = found[0]
        if handler.language not in _LANGUAGES or handler.group_sources:
            return None
        if target.get_option(OPTION) is None or not get_var(ENABLE_VAR, True):
            return None
        owner = _owner(target)
        key = (id(owner), handler.language)
        if key not in self._builds:
            self._builds[key] = self._make(owner, handler.language)
        build = self._builds[key]
        if build is not None and owner is not target:
            if (id(target), handler.language) not in self._checked:
                self._checked.add((id(target), handler.language))
                self._check_reuse(target, build, found, effective, env)
        return build

    def _make(self, owner: Target, language: str) -> PrecompiledHeaderBuild | None:
        """Plan *owner*'s precompiled header for *language*, if it lists
        headers for it, has sources in it and its toolchain makes one."""
        headers = _headers(owner, language)
        env = owner._env
        found = self._source_handler(owner, language)
        if not headers or env is None or found is None:
            return None
        handler, toolchain = found
        _, header_suffix, source_suffix = _LANGUAGES[language]
        from pcons.tools.compile_link import object_dir_name

        directory = owner.build_dir / object_dir_name(owner) / "pch"
        wrapper = directory / f"{language}_pch{header_suffix}"
        spec = toolchain.precompiled_header(language, _on_disk(owner.project, wrapper))
        if spec is None:
            return None

        project = owner.project
        lines: list[str] = []
        database_flags: list[str] = []
        header_nodes: list[FileNode] = []
        for header in headers:
            if isinstance(header, str) and header.startswith("<"):
                lines.append(f"#include {header}")
                database_flags.append(spec.include_flag + header.strip("<>"))
                continue
            path = project.top_path_resolver.anchor_script_path(header, owner._subdir)
            node = project._node(path)
            on_disk = _on_disk(project, node.path)
            lines.append(f'#include "{on_disk.as_posix()}"')
            database_flags.append(spec.include_flag + str(on_disk))
            header_nodes.append(node)
        write_text_if_changed(
            _on_disk(project, wrapper),
            f"// The headers {owner.name} precompiles, written by pcons.\n"
            + "\n".join(lines)
            + "\n",
        )

        effective = self._factory._compute_requirements(
            owner, env, handler.tool_name, toolchain
        )
        context = CompileLinkContext.from_effective_requirements(
            effective, mode="compile", tool_name=handler.tool_name, env=env
        )
        settings = _settings(context, env, handler.tool_name)
        context.flags.extend(spec.build_flags)

        pch_node = project._node(spec.path)
        pch_path = pch_node.path
        if spec.from_source:
            source = directory / f"{language}_pch{source_suffix}"
            write_text_if_changed(
                _on_disk(project, source),
                f"/* Compiled to make {pch_path.name}, written by pcons. */\n",
            )
            input_node = project._node(source)
            compile_path = source.with_name(source.name + handler.object_suffix)
        else:
            input_node = project._node(wrapper)
            compile_path = pch_path
        compile_node = project._node(compile_path)
        compile_node.add_inputs([input_node])
        compile_node._build_info = {
            "tool": handler.tool_name,
            "command_var": handler.command_var,
            "language": language,
            "sources": [input_node],
            "depfile": self._factory._resolve_depfile(handler.depfile, compile_path),
            "deps_style": handler.deps_style,
            "context": context,
            "env": env,
            "producer": owner,
            "toolchain": toolchain,
        }
        if spec.from_source:
            compile_node._build_info["outputs"] = {
                "primary": {"path": compile_path, "suffix": compile_path.suffix},
                "pch": {"path": pch_path, "suffix": pch_path.suffix, "implicit": True},
            }
            pch_node._build_info = {"primary_node": compile_node}
        # A listed header that is generated is built first.
        compile_node.wait_for(header_nodes)
        env.register_node(compile_node)
        return PrecompiledHeaderBuild(
            owner=owner,
            language=language,
            toolchain=toolchain,
            pch=pch_node,
            compile_node=compile_node,
            link_object=compile_node if spec.from_source else None,
            use_flags=list(spec.use_flags),
            database_flags=database_flags,
            settings=settings,
        )

    def _source_handler(
        self, target: Target, language: str
    ) -> tuple[SourceHandler, Toolchain] | None:
        """The handler and toolchain of *target*'s first source in
        *language* that compiles with the target's own environment."""
        env = target._env
        if env is None:
            return None
        for source in target.sources:
            if not isinstance(source, FileNode):
                continue
            if target._source_envs.get(source.path, env) is not env:
                continue
            found = self._factory._get_source_handler_with_toolchain(source.path, env)
            if found is not None and found[0].language == language:
                return found
        return None

    def _check_reuse(
        self,
        target: Target,
        build: PrecompiledHeaderBuild,
        found: tuple[SourceHandler, Toolchain],
        effective: EffectiveRequirements,
        env: Environment,
    ) -> None:
        """Refuse a target whose sources compile differently from those of
        the target whose precompiled header it reuses."""
        handler, toolchain = found
        name = _LANGUAGES[build.language][0]
        owner = build.owner.name
        context = CompileLinkContext.from_effective_requirements(
            effective, mode="compile", tool_name=handler.tool_name, env=env
        )
        mine = _settings(context, env, handler.tool_name)
        differences = [
            f"  {what}: {' '.join(mine[what]) or '(none)'} here, "
            f"{' '.join(build.settings[what]) or '(none)'} in '{owner}'"
            for what in mine
            if mine[what] != build.settings[what]
        ]
        if toolchain is not build.toolchain:
            differences.insert(
                0,
                f"  compiler: {toolchain.name} here, {build.toolchain.name} in '{owner}'",
            )
        if differences:
            raise PconsValueError(
                f"Target '{target.name}' reuses the precompiled {name} header of "
                f"'{owner}', but compiles its {name} sources differently, so the "
                f"compiler couldn't use it:\n"
                + "\n".join(differences)
                + f"\n  Compile them the same way, or give '{target.name}' its "
                f"own headers to precompile.",
                location=target.defined_at,
            )


def _owner(target: Target) -> Target:
    """The target whose precompiled headers *target* uses: itself, or the
    one its option names, followed through any that reuse in turn."""
    owner = target
    seen = [target]
    while isinstance(value := owner.get_option(OPTION), Target):
        if value in seen:
            chain = " -> ".join(t.name for t in [*seen, value])
            raise PconsValueError(
                f"Targets reuse each other's precompiled headers in a cycle: {chain}",
                location=target.defined_at,
            )
        if value.get_option(OPTION) is None:
            raise PconsValueError(
                f"Target '{owner.name}' reuses the precompiled headers of "
                f"'{value.name}', which has none. Set precompile_headers on "
                f"'{value.name}'.",
                location=owner.defined_at,
            )
        seen.append(value)
        owner = value
    return owner


def _headers(owner: Target, language: str) -> list[str | Path]:
    """The headers *owner* precompiles for *language*, as it gave them."""
    value = owner.get_option(OPTION)
    if isinstance(value, dict):
        unknown = sorted(set(value) - set(_LANGUAGES))
        if unknown:
            raise PconsValueError(
                f"precompile_headers of '{owner.name}' names unknown languages "
                f"{unknown}; the languages are {sorted(_LANGUAGES)}.",
                location=owner.defined_at,
            )
        value = value.get(language, [])
    if isinstance(value, (str, Path)):
        return [value]
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (str, Path)) for item in value
    ):
        return list(value)
    raise PconsTypeError(
        f"precompile_headers of '{owner.name}' takes header paths or <system> "
        f"names, a {{language: headers}} dict, or a target to reuse; got "
        f"{value!r}",
        location=owner.defined_at,
    )


def _settings(
    context: CompileLinkContext, env: Environment, tool_name: str
) -> dict[str, list[str]]:
    """What a precompiled header has to agree with a compile on."""
    tool = getattr(env, tool_name, None)
    return {
        "defines": [str(d) for d in context.defines],
        "flags": [str(f) for f in [*getattr(tool, "flags", []), *context.flags]],
    }


def _on_disk(project: Project, path: Path) -> Path:
    """Where the file at node path *path* is."""
    return path if path.is_absolute() else project.top.root_dir / path
