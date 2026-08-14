# SPDX-License-Identifier: MIT
"""Rendering for ``pcons explain``.

The command's logic (run the script, resolve, select targets) lives in
`pcons.cli`; this module turns the resolved project into the report:

    ## Explanation of Targets and Environments

    === simulator  (program)  [env #1]
      * build/obj.simulator/main.o  <-  main.c
          /usr/bin/clang -Iinclude -c -o build/obj.simulator/main.o main.c
      requirements:
        include_dirs:
          include  <- math (public)

    Environment #1  (toolchain: llvm)
      cc.flags:
        -O2  <- release (variant)

Top-level targets are set off with a leading ``===``; each built file gets a
bullet. With color enabled the bullets become ``•``, names and section
headers gain ANSI styling, and truncation uses ``…``; the plain-ASCII form
is what tests and piped output see. Command lines are truncated to the
requested width (terminal width by default on a tty; unlimited when piped).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from pcons.core.environment import Environment
    from pcons.core.node import FileNode
    from pcons.core.project import Project
    from pcons.core.target import Target


def resolve_color(mode: str) -> bool:
    """Whether to emit ANSI styling: the --color choice, defaulting to
    "color when stdout is a terminal and NO_COLOR is unset"."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def resolve_width(width: int | None) -> int:
    """The line width to truncate to; 0 means unlimited.

    Defaults to the terminal's width on a tty. Piped output gets no limit:
    a pager or grep wants the whole line.
    """
    if width is not None:
        return max(0, width)
    if sys.stdout.isatty():
        return shutil.get_terminal_size().columns
    return 0


class _Style:
    """The report's glyphs and colors, in plain and fancy variants."""

    def __init__(self, color: bool) -> None:
        self.color = color
        self.bullet = "•" if color else "*"
        self.ellipsis = "…" if color else "..."

    def paint(
        self,
        text: str,
        *,
        fg: str | None = None,
        bold: bool = False,
        dim: bool = False,
    ) -> str:
        if not self.color:
            return text
        return click.style(text, fg=fg, bold=bold, dim=dim)

    def heading(self, text: str) -> str:
        return self.paint(text, bold=True)

    def title(self, text: str) -> str:
        if not self.color:
            return text
        return click.style(text, bold=True, underline=True)

    def target_name(self, text: str) -> str:
        return self.paint(text, fg="cyan", bold=True)

    def env_ref(self, text: str) -> str:
        return self.paint(text, fg="magenta")

    def origin(self, text: str) -> str:
        return self.paint(text, fg="green")

    def dim(self, text: str) -> str:
        return self.paint(text, dim=True)


def _collapse_paths(paths: list[str]) -> list[str]:
    """Collapse sibling paths shell-style: ``build/foo/{a.o,b.o}``.

    Groups by directory, in the order the directories first appear; a
    directory contributing a single file keeps its plain spelling. Display
    only — commands always show their literal paths.
    """
    groups: dict[str, list[str]] = {}
    for path in paths:
        directory, _, name = path.rpartition("/")
        groups.setdefault(directory, []).append(name)
    collapsed = []
    for directory, names in groups.items():
        prefix = f"{directory}/" if directory else ""
        if len(names) == 1:
            collapsed.append(prefix + names[0])
        else:
            collapsed.append(prefix + "{" + ",".join(names) + "}")
    return collapsed


def _fit(line: str, width: int, style: _Style) -> str:
    """Truncate *line* to *width* columns, marking the cut. Only applied to
    unstyled text, so the count is honest."""
    if width and len(line) > width:
        keep = max(1, width - len(style.ellipsis))
        return line[:keep] + style.dim(style.ellipsis)
    return line


def _format_requirements(target: Target, root: Path, style: _Style) -> Iterator[str]:
    """The target's effective requirements, each value with its contributor.

    Recomputes what the resolver computed for the target's commands (the
    computation is pure), reading the origins the merge recorded: compile
    fields from the compile-phase requirements, link fields from the
    link-phase ones (they differ in which implicit deps apply).
    """
    from pcons.core.explain import node_paths
    from pcons.core.target import Target
    from pcons.tools.requirements import compute_effective_requirements, flag_units

    env = target._env
    if env is None:
        return

    compile_reqs = compute_effective_requirements(target, env, for_compilation=True)
    link_reqs = compute_effective_requirements(target, env, for_compilation=False)

    # (field, display value, origin) for every value, in merge order.
    rows: list[tuple[str, str, tuple[str, str] | None]] = []
    for field, values, reqs in (
        ("include_dirs", compile_reqs.includes, compile_reqs),
        ("system_include_dirs", compile_reqs.system_includes, compile_reqs),
        ("defines", compile_reqs.defines, compile_reqs),
        ("compile_flags", compile_reqs.compile_flags, compile_reqs),
        ("link_flags", link_reqs.link_flags, link_reqs),
        ("link_libs", link_reqs.link_libs, link_reqs),
        ("link_dirs", link_reqs.link_dirs, link_reqs),
    ):
        if field in ("compile_flags", "link_flags"):
            # Units, not tokens: "-framework Foo" is one row, keyed the way
            # the merge recorded it, so repeated flag tokens keep their own
            # attributions.
            for unit in flag_units(
                values, reqs.separated_arg_flags, reqs.passthrough_flags
            ):
                rows.append((field, unit, reqs.origins.get((field, unit))))
            continue
        for value in values:
            if isinstance(value, Target):
                key = display = value.name or "?"
            else:
                key = str(value)
                display = (
                    node_paths([value], root)[0] if isinstance(value, Path) else key
                )
            rows.append((field, display, reqs.origins.get((field, key))))

    # A value can legitimately appear twice with one meaning (a target in
    # link_libs plus the same name as a plain lib); one row tells the story.
    seen: set[tuple[str, str, tuple[str, str] | None]] = set()
    rows = [row for row in rows if not (row in seen or seen.add(row))]

    if not rows:
        return

    yield "  requirements:"
    # One long value (an SDK path) must not push every attribution off to
    # the right. Align to the true longest when that is reasonable; past
    # the threshold, align to the longest reasonable row and let the long
    # ones overflow visibly.
    lengths = [len(display) for _, display, _ in rows]
    pad = (
        max(lengths)
        if max(lengths) <= 48
        else max((n for n in lengths if n <= 48), default=48)
    )
    current_field = None
    for field, display, origin in rows:
        if field != current_field:
            current_field = field
            yield f"    {field}:"
        if origin is None:
            attribution = "<- (unknown)"
        else:
            owner, scope = origin
            attribution = f"<- {owner} ({scope})"
        yield f"      {display.ljust(pad)}  {style.origin(attribution)}"


def render_explanation(
    project: Project,
    targets: list[Target],
    *,
    explicit_targets: bool,
    color: bool,
    width: int,
) -> Iterator[str]:
    """The whole report, line by line.

    Args:
        project: The resolved project.
        targets: The targets to explain, in order.
        explicit_targets: True when the user named targets; suppresses the
            "commands with no target" section, which belongs to none of them.
        color: Emit ANSI styling (and the fancier glyphs that go with it).
        width: Truncate command lines to this many columns; 0 for unlimited.
    """
    from pcons.core.explain import (
        CommandFrame,
        Explanation,
        format_node_command,
        node_paths,
    )
    from pcons.core.node import FileNode

    style = _Style(color)
    root = project.root_dir.absolute()
    frame = CommandFrame.for_project(project)

    def fallback_command(build_info: Mapping[str, object]) -> list | None:
        """The standalone install/archive template for an env-less node.

        The resolver only expands commands for nodes that carry an
        environment; for the rest the generators fall back to the standalone
        tools, and this is the same lookup so explain shows what they emit.
        """
        if build_info.get("env") is not None:
            return None
        tool_name = build_info.get("tool")
        command_var = build_info.get("command_var")
        if not isinstance(tool_name, str) or not isinstance(command_var, str):
            return None
        from pcons.generators.generator import apply_context_overrides
        from pcons.tools.tool import standalone_tool_tokens

        tokens = standalone_tool_tokens(tool_name, command_var)
        overrides = getattr(build_info.get("context"), "get_env_overrides", None)
        if tokens is not None and callable(overrides):
            tokens = apply_context_overrides(tokens, tool_name, overrides())
        return tokens

    def node_command(node: FileNode) -> str | None:
        build_info = node._build_info or {}
        return format_node_command(node, frame, fallback_command(build_info))

    # The project's root, in full: -C means the cwd is not where the user is
    # sitting, so a relative spelling would usually just say ".".
    home = Path.home()
    where = (
        f"~/{root.relative_to(home).as_posix()}"
        if root.is_relative_to(home)
        else root.as_posix()
    )
    yield style.title(f"## Explanation of Targets and Environments: {where}")
    yield style.dim(
        f"Commands are shown as the build runs them, from the build "
        f"directory ({Path(project.build_dir).as_posix()})."
    )
    yield ""

    # Stable labels for every environment (name, or position in the project),
    # so a target's header and the provenance section below agree.
    env_labels = {
        id(env): env.name or f"#{index}"
        for index, env in enumerate(project.environments, 1)
    }
    used_envs: dict[int, Environment] = {}

    def env_label(env: Environment | None) -> str | None:
        if env is None:
            return None
        used_envs.setdefault(id(env), env)
        return env_labels.get(id(env), "?")

    def env_ref(env: Environment | None) -> str:
        return style.env_ref(f"[env {env_label(env)}]")

    def node_lines(
        node: FileNode, relative_to_env: Environment | None
    ) -> Iterator[str]:
        """One node's bullet line and command; nothing if it has no command."""
        command = node_command(node)
        if command is None:
            return
        build_info = node._build_info or {}
        sources = ", ".join(
            _collapse_paths(node_paths(build_info.get("sources") or [], root))
        )
        arrow = f"  {style.bullet} {node.path.as_posix()}"
        if sources:
            arrow += f"  <-  {sources}"
        # A node built with an env other than the section's (a per-source
        # or per-node override) is flagged; the common case stays unannotated.
        node_env = build_info.get("env")
        if node_env is not None and node_env is not relative_to_env:
            arrow += f"  {env_ref(node_env)}"
        yield arrow
        yield _fit(f"      {command}", width, style)

    def defined_at(obj: object) -> str | None:
        """Where *obj* was defined, as file:line relative to the project."""
        loc = getattr(obj, "defined_at", None)
        filename = getattr(loc, "filename", None)
        if not filename:
            return None
        p = Path(filename)
        if p.is_absolute() and p.is_relative_to(root):
            filename = p.relative_to(root).as_posix()
        return f"{filename}:{getattr(loc, 'lineno', '?')}"

    def target_header(title: str, detail: str, env: Environment | None) -> str:
        line = f"=== {style.target_name(title)}"
        if detail:
            line += f"  {style.dim(detail)}"
        if env is not None:
            line += f"  {env_ref(env)}"
        return line

    shown: set[FileNode] = set()
    for target in targets:
        target_env = target._env
        detail = f"({target.target_type})" if target.target_type else ""
        header = target_header(target.name or "?", detail, target_env)
        location = defined_at(target)
        if location is not None:
            header += f"  {style.dim(location)}"
        yield header

        # A test target builds nothing itself; it declares a command for the
        # test runner. One line says what runs, instead of an empty section.
        spec = (
            (target._builder_data or {}).get("spec")
            if target.target_type == "test"
            else None
        )
        if spec is not None:
            details = []
            if getattr(spec, "labels", ()):
                details.append("labels: " + ", ".join(spec.labels))
            timeout = getattr(spec, "timeout", None)
            if timeout:
                details.append(f"timeout: {timeout:g}s")
            details.extend(
                name.replace("_", "-")
                for name in ("should_fail", "serial", "disabled")
                if getattr(spec, name, False)
            )
            line = "  runs: " + " ".join(str(c) for c in spec.command)
            if details:
                line += style.dim("  (" + "; ".join(details) + ")")
            yield line
            yield ""
            continue

        for node in [*target.intermediate_nodes, *target.output_nodes]:
            if not isinstance(node, FileNode) or node in shown:
                continue
            shown.add(node)
            yield from node_lines(node, target_env)

        other_env_deps = [
            f"{dep.name} {env_ref(dep._env)}"
            for dep in target.dependencies
            if dep._env is not None and dep._env is not target_env
        ]
        if other_env_deps:
            yield f"  deps in other environments: {', '.join(other_env_deps)}"

        yield from _format_requirements(target, root, style)
        yield ""

    # Builds composed with node-level builders (env.cc.Object(...)) rather
    # than project targets still have commands worth explaining; show any
    # built node no target section covered. Skipped when targets were named,
    # since these belong to none of them.
    if not explicit_targets:
        for env in project.environments:
            orphans = [
                node
                for node in getattr(env, "_created_nodes", [])
                if isinstance(node, FileNode)
                and node not in shown
                and node_command(node) is not None
            ]
            if not orphans:
                continue
            yield target_header("commands with no target", "", env)
            for node in orphans:
                shown.add(node)
                yield from node_lines(node, env)
            yield ""

    if not used_envs:
        # A project can build with no environments at all (pure install
        # projects); say so rather than ending the report abruptly.
        if project.environments:
            yield style.dim("(no environments used by the targets shown)")
        else:
            yield style.dim("(no environments defined)")
        yield ""

    for env in used_envs.values():
        title = style.heading(f"Environment {env_labels.get(id(env), '?')}")
        toolchain = env._toolchain
        if toolchain is not None:
            title += f"  {style.dim(f'(toolchain: {toolchain.name})')}"
        location = defined_at(env)
        if location is not None:
            title += f"  {style.dim(location)}"

        # Users read this section for what *they* shaped: presets, knobs and
        # manual edits. Toolchain-baseline rows (ar's "rcs", MSVC's /nologo)
        # are the tool's own defaults, so they are dropped; an environment
        # with nothing else collapses to one line.
        explanation = env.explain()
        shaped = Explanation(
            tuple(row for row in explanation.rows if row.category != "toolchain"),
            explanation.imperative,
        )
        if shaped:
            yield title
            for line in str(shaped).splitlines():
                yield f"  {line}"
        else:
            note = "toolchain defaults only" if explanation.rows else "no flags"
            yield f"{title}  {style.dim(f'({note})')}"
        yield ""
