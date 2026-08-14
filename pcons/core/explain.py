# SPDX-License-Identifier: MIT
"""Explain where a tool's flags came from.

Provenance is *derived*, not recorded per flag: an :class:`~pcons.core.preset.Preset`
carries the exact tokens it contributes, and an environment keeps the ordered
list of presets applied to it. :func:`explain` replays that list against a tool's
current flag/define lists and attributes each token to the preset that added it.
Tokens not produced by any preset (toolchain defaults, or direct
``env.cc.flags.append(...)``) are labelled ``(manual)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.preset import Preset


@dataclass(frozen=True)
class ExplainRow:
    """One token of a tool variable and where it came from.

    ``source`` is the contributing preset's name, or ``None`` if the token was
    not produced by any preset (a toolchain default or a manual edit).
    """

    tool: str
    var: str  # "flags", "defines", or "cmd"
    token: str
    source: str | None
    category: str | None


@dataclass(frozen=True)
class Explanation:
    """The attributed tokens of one or more tools, with a readable ``str``.

    ``imperative`` lists ``(name, description)`` of any imperative escape-hatch
    presets that ran — these mutate the environment directly, so their effect
    can't be attributed token-by-token; they're reported as a trailing note.
    """

    rows: tuple[ExplainRow, ...]
    imperative: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rows) or bool(self.imperative)

    def __str__(self) -> str:
        lines: list[str] = []
        if self.rows:
            # Group rows by "tool.var", preserving first-seen order.
            groups: dict[str, list[ExplainRow]] = {}
            for row in self.rows:
                groups.setdefault(f"{row.tool}.{row.var}", []).append(row)

            width = max(len(r.token) for r in self.rows)
            for key, rows in groups.items():
                lines.append(f"{key}:")
                for r in rows:
                    if r.source is None:
                        origin = "<- (manual)"
                    else:
                        origin = f"<- {r.source} ({r.category})"
                        if r.var == "cmd":
                            origin += " [replaced]"
                    lines.append(f"  {r.token.ljust(width)}  {origin}")
        if self.imperative:
            lines.append("imperative presets (ran; effect not attributable):")
            for name, desc in self.imperative:
                lines.append(f"  {name} - {desc}" if desc else f"  {name}")
        return "\n".join(lines) if lines else "(no flags)"


def _quote(token: str) -> str:
    """Quote a rendered token for display when it contains whitespace."""
    if token and not any(c.isspace() for c in token):
        return token
    return '"' + token.replace('"', '\\"') + '"'


def node_paths(nodes: Sequence[Any], root: Any = None) -> list[str]:
    """The posix paths of *nodes*, tolerating plain strings.

    An absolute path under *root* is shown relative to it: the command runs
    the same either way, and the short spelling is the one the reader knows.
    """
    paths: list[str] = []
    for n in nodes:
        path = getattr(n, "path", n)
        if root is not None and hasattr(path, "is_relative_to"):
            if path.is_absolute() and path.is_relative_to(root):
                path = path.relative_to(root)
        paths.append(path.as_posix() if hasattr(path, "as_posix") else str(path))
    return paths


@dataclass(frozen=True)
class CommandFrame:
    """The directory commands run in (the build directory), for spelling
    their paths exactly as the generators do.

    Ninja and make both execute from the build directory, so a command shown
    in this frame is the one the build actually runs — and the one a user can
    paste into a shell there. The anchoring rules mirror the generators':
    built paths are build-relative, plain sources get the ``topdir`` prefix
    (the relative path back to the project root), external absolutes pass
    through.
    """

    root: Any  # absolute Path of the project root
    build_dir: Any  # absolute Path of the build directory
    build_dir_parts: tuple[str, ...]  # canonical node-path prefix, if relative
    topdir: str  # relative path from the build dir back to the project root

    @classmethod
    def for_project(cls, project: Any) -> CommandFrame:
        import os

        # resolve(), as the generators do (ninja resolves both dirs), so a
        # symlinked root or build dir spells the same topdir build.ninja has.
        root = Path(project.root_dir).resolve()
        build_dir = Path(project.build_dir)
        build_abs = (
            build_dir if build_dir.is_absolute() else root / build_dir
        ).resolve()
        parts = () if build_dir.is_absolute() else build_dir.parts
        try:
            topdir = os.path.relpath(root, build_abs).replace(os.sep, "/")
        except ValueError:  # Windows: different drives
            topdir = root.as_posix()
        return cls(root, build_abs, parts, topdir)

    def for_cwd(self, cwd: Any) -> CommandFrame:
        """The frame for an edge that runs in *cwd* rather than the build
        directory (``env.Command(cwd=...)``); the generators render such an
        edge's paths as seen from there."""
        import os

        cwd_abs = Path(cwd)
        try:
            topdir = os.path.relpath(self.root, cwd_abs).replace(os.sep, "/")
        except ValueError:  # Windows: different drives
            topdir = self.root.as_posix()
        return CommandFrame(self.root, cwd_abs, (), topdir)

    def _anchored(self, rel: str) -> str:
        return rel if self.topdir == "." else f"{self.topdir}/{rel}"

    def spell(self, path: Any, *, built: bool) -> str:
        """One path as the command sees it from this frame's directory."""
        from pcons.core.paths import execution_relative

        text = execution_relative(
            path, execution_dir=self.build_dir, build_dir_parts=self.build_dir_parts
        )
        p = Path(path)
        if text != str(p).replace("\\", "/"):
            # Rewritten: absolute under the execution dir, or carrying the
            # build-dir prefix (stripped).
            return text
        if p.is_absolute():
            if p.is_relative_to(self.root):
                return self._anchored(p.relative_to(self.root).as_posix())
            return text
        if built and self.build_dir_parts:
            # Canonical build-anchored node form (a bare name sits at the
            # build root, as the generators treat it).
            return text
        # A root-anchored relative path, as seen from the execution dir.
        return self._anchored(text)

    def spell_node(self, n: Any) -> str:
        """A node's path in command spelling.

        The generators' rule: a node something builds (it has build info or
        is a target) is build-anchored; anything else is a source.
        """
        path = getattr(n, "path", None)
        if path is None:
            return str(n)
        built = getattr(n, "_build_info", None) is not None or bool(
            getattr(n, "is_target", False)
        )
        return self.spell(path, built=built)


def format_node_command(
    node: Any,
    frame: CommandFrame | None = None,
    fallback_command: list[Any] | None = None,
) -> str | None:
    """Render a resolved node's command as one human-readable line.

    The resolver leaves each built node's command in ``_build_info["command"]``
    as a token list with :class:`~pcons.core.subst.SourcePath` /
    :class:`~pcons.core.subst.TargetPath` markers still in place, for the
    generators to spell in their own syntax (``$in``/``$out`` for ninja).
    Here the markers become the node's actual paths, spelled in *frame* —
    the build directory the command runs in — so ``pcons explain`` shows,
    concretely, what the build tool executes.

    Returns None for a node that carries no command of its own (a source, or
    a secondary output whose primary node owns the edge). *fallback_command*
    is a token list to render when the node has no resolver-expanded command —
    the standalone install/archive tools' templates, which the generators
    expand the same way.
    """
    from pcons.core.subst import NodeVar, PathToken, SourcePath, TargetPath

    build_info = getattr(node, "_build_info", None)
    if not build_info or "primary_node" in build_info:
        return None
    command = build_info.get("command", fallback_command)
    if command is None:
        return None

    # An edge with a cwd runs there, not in the build dir; the generators
    # spell its paths from there and wrap the command in a cd. Same here,
    # so the shown line stays the one that runs (pasteable from build dir).
    cd_prefix: list[str] = []
    cwd = build_info.get("cwd")
    if frame is not None and cwd is not None:
        import os

        try:
            cd_to = os.path.relpath(Path(cwd), frame.build_dir).replace(os.sep, "/")
        except ValueError:  # Windows: different drives
            cd_to = Path(cwd).as_posix()
        cd_prefix = ["cd", cd_to, "&&"]
        frame = frame.for_cwd(cwd)

    def spell_nodes(nodes: Sequence[Any]) -> list[str]:
        if frame is None:
            return node_paths(nodes)
        return [frame.spell_node(n) for n in nodes]

    sources = spell_nodes(build_info.get("sources") or [])
    node_vars = build_info.get("vars") or {}
    self_path = spell_nodes([node])[0]

    def output_paths() -> list[str]:
        """The edge's outputs, in ``target_N`` order — from whichever key
        this edge's builder used, with ninja's precedence: the ``outputs``
        info dict (MSVC-style DLL + import lib), then ``all_targets``
        (env.Command), then ``all_output_nodes``."""
        outputs_info = build_info.get("outputs")
        if isinstance(outputs_info, dict) and outputs_info:
            paths = [
                info["path"]
                for info in outputs_info.values()
                if isinstance(info, dict) and "path" in info
            ]
            if paths:
                if frame is None:
                    return [Path(p).as_posix() for p in paths]
                return [frame.spell(p, built=True) for p in paths]
        nodes_dict = build_info.get("all_output_nodes")
        node_list = build_info.get("all_targets") or (
            list(nodes_dict.values()) if nodes_dict else []
        )
        return spell_nodes(node_list) if node_list else [self_path]

    outputs = output_paths()
    topdir = frame.topdir if frame is not None else "."

    if isinstance(command, str):
        # A string command is already a shell line; substituting into it and
        # re-quoting it as one token would wrap the whole line in quotes.
        # \b so "$install.destdir" and friends are left alone; a callable
        # replacement so backslashes in paths are not re-interpreted.
        text = command.replace("$SRCDIR", topdir)
        text = re.sub(r"\$in\b", lambda _: " ".join(sources), text)
        text = re.sub(r"\$out\b", lambda _: " ".join(outputs), text)
        parts = [_quote(str(t)) for t in cd_prefix]
        parts.extend(_quote(str(t)) for t in build_info.get("launcher") or [])
        parts.append(text)
        parts.extend(str(f) for f in build_info.get("extra_command_flags") or [])
        return " ".join(parts)

    def render(token: Any) -> list[str]:
        if isinstance(token, list):
            # A list-valued variable (a toolchain's per-node flag list) is
            # several tokens; an empty one is none at all — as the
            # generators expand it, not as its repr.
            return [part for item in token for part in render(item)]
        if isinstance(token, PathToken):
            # The generator contract: relative "project" paths get the topdir
            # prefix; absolutes and "build" paths pass through. Posix
            # separators throughout, as everywhere in this display.
            path = Path(token.path).as_posix() if token.path else ""
            if (
                token.path_type == "project"
                and path
                and not Path(path).is_absolute()
                and topdir != "."
            ):
                path = f"{topdir}/{path}"
            return [token.prefix + path + token.suffix]
        if isinstance(token, (SourcePath, TargetPath)):
            paths = sources if isinstance(token, SourcePath) else outputs
            if getattr(token, "basename", False):
                paths = [self_path.rpartition("/")[2]]
            elif token.index is not None:
                paths = paths[token.index : token.index + 1]
            elif token.start is not None or token.stop is not None:
                paths = paths[token.start : token.stop]
            return [token.prefix + p + token.suffix for p in paths]
        if isinstance(token, NodeVar):
            value = node_vars.get(token.name, f"${token.name}")
            if isinstance(value, (list, PathToken, SourcePath, TargetPath)):
                return render(value)
            return [str(value)]
        text = str(token)
        # Ninja spellings that survive into templates via $$ escapes.
        if text == "$in":
            return list(sources)
        if text == "$out":
            return list(outputs)
        # $SRCDIR is the project source root, spelled from the build dir.
        return [text.replace("$SRCDIR", topdir)]

    tokens = command if isinstance(command, list) else [command]
    rendered = [
        part
        for token in [*cd_prefix, *(build_info.get("launcher") or []), *tokens]
        for part in render(token)
        if part
    ]
    extra = build_info.get("extra_command_flags")
    if extra:
        rendered.extend(str(f) for f in extra)
    return " ".join(_quote(t) for t in rendered)


def _attribute(
    actual: Sequence[Any],
    expected: Sequence[tuple[str, str, str]],
) -> list[tuple[str, str | None, str | None]]:
    """Match preset-contributed tokens against a tool's actual token list.

    Presets only ever append, in order, so their tokens form a subsequence of
    the actual list. Walk the actual list greedily, advancing through
    ``expected`` on each match; unmatched tokens are manual.
    """
    result: list[tuple[str, str | None, str | None]] = []
    j = 0
    for tok in actual:
        if j < len(expected) and expected[j][0] == tok:
            _, source, category = expected[j]
            result.append((tok, source, category))
            j += 1
        else:
            result.append((tok, None, None))
    return result


def explain(
    applied_presets: Sequence[Preset],
    tools: dict[str, dict[str, object]],
    imperative: Sequence[tuple[str, str]] = (),
    separated_arg_flags: frozenset[str] = frozenset(),
    passthrough_flags: frozenset[str] = frozenset(),
) -> Explanation:
    """Build an :class:`Explanation` for the given tools.

    Args:
        applied_presets: Presets applied to the environment, in order.
        tools: ``{tool_name: {"flags": [...], "defines": [...], "cmd": value}}``
            snapshot of each tool's current values.
        imperative: ``(name, description)`` of imperative presets that ran.
        separated_arg_flags: Flags taking a separate argument; a flag and its
            argument (``-framework Foo``) form one attributed row.
        passthrough_flags: Driver flags whose argument goes to a sub-tool
            (``-Xlinker``); a run of them forms one row.
    """
    from pcons.core.flags import parse_flags

    def units(tokens: Sequence[Any]) -> list[str]:
        return [
            str(group)
            for group in parse_flags(
                list(tokens), separated_arg_flags, passthrough_flags
            )
        ]

    rows: list[ExplainRow] = []
    for tool_name, values in tools.items():
        for var in ("flags", "defines"):
            actual = values.get(var)
            if not isinstance(actual, list) or not actual:
                continue
            # Flags attribute as units, grouped the same way on both sides;
            # a contribution's flags are self-contained, so pairs never
            # straddle preset boundaries.
            actual_units = units(actual) if var == "flags" else actual
            expected: list[tuple[str, str, str]] = [
                (token, preset.name, preset.category)
                for preset in applied_presets
                for contribution in preset.contributions
                if contribution.tool == tool_name
                for token in (
                    units(contribution.flags)
                    if var == "flags"
                    else contribution.defines
                )
            ]
            for token, source, category in _attribute(actual_units, expected):
                rows.append(ExplainRow(tool_name, var, token, source, category))

        # cmd is replaced (not appended); attribute to the last preset that set
        # it, but only show a row when a preset actually replaced the command.
        cmd = values.get("cmd")
        if isinstance(cmd, str):
            # (preset_name, category, cmd_value) of the last preset to set cmd
            replaced: tuple[str, str, str] | None = None
            for preset in applied_presets:
                for contribution in preset.contributions:
                    if contribution.tool == tool_name and contribution.cmd is not None:
                        replaced = (preset.name, preset.category, contribution.cmd)
            if replaced is not None and cmd == replaced[2]:
                rows.append(ExplainRow(tool_name, "cmd", cmd, replaced[0], replaced[1]))

    return Explanation(tuple(rows), tuple(imperative))
