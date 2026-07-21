# SPDX-License-Identifier: MIT
"""compile_commands.json generator for IDE integration.

Generates a compile_commands.json file that IDEs and tools like
clang-tidy can use for code intelligence.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.node import FileNode
from pcons.generators.generator import BaseGenerator
from pcons.toolchains.build_context import CompileLinkContext

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target

logger = logging.getLogger(__name__)


class CompileCommandsGenerator(BaseGenerator):
    """Generator for compile_commands.json.

    Creates a JSON compilation database in the format expected by
    clang tools, IDEs, and language servers, in <build_dir>.
    """

    # Languages that should be included in compile_commands.json
    COMPILE_LANGUAGES = {"c", "cxx", "cpp", "objc", "objcxx", "cuda", "swift"}

    def __init__(self, *, root_symlink: bool = True) -> None:
        """Args:
        root_symlink: Maintain the project-root compile_commands.json
            symlink (see BaseGenerator.generate); False keeps all output
            inside build_dir.
        """
        super().__init__("compile_commands")
        self._root_symlink = root_symlink

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate compile_commands.json in output_dir."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "compile_commands.json"

        commands: list[dict[str, Any]] = []

        for target in project.targets:
            commands.extend(self._collect_compile_commands(target, project))

        with open(output_file, "w") as f:
            json.dump(commands, f, indent=2)
            f.write("\n")

        if self._root_symlink:
            self._create_root_symlink(output_file, project)

    def _create_root_symlink(self, output_file: Path, project: Project) -> None:
        """Create a symlink to compile_commands.json in the project root
        so clangd/IDEs find it without configuration.

        Created atomically (temp symlink + os.replace) so concurrent
        generate() runs cannot race on it. Failure to create (e.g. Windows
        without privileges) logs a warning.
        """
        root_dir = project.root_dir
        link_path = root_dir / "compile_commands.json"

        # If build_dir is the project root, the file is already there. Compare
        # the directories — never resolve() the link itself, which can race
        # (EINVAL) with a concurrent generate() swapping it.
        if output_file.parent.resolve() == root_dir.resolve():
            return

        try:
            target_path = os.path.relpath(output_file, root_dir)
        except ValueError:
            # On Windows, relpath fails across drive letters
            return

        # Inspect the existing entry. This is best-effort: a concurrent writer
        # may be swapping the link, and on macOS a stat/lstat racing a rename
        # raises EINVAL (which pathlib does not suppress), so tolerate any
        # OSError and fall through to the atomic swap below.
        try:
            if link_path.is_symlink():
                # Fast path: already pointing where we want.
                if Path(os.readlink(link_path)) == Path(target_path):
                    return
            elif link_path.exists():
                # A real file the user put there — don't clobber it.
                logger.warning(
                    "compile_commands.json exists at project root as a "
                    "regular file; not replacing with symlink"
                )
                return
        except OSError:
            pass

        # Atomic create-or-replace via a unique temp name, so parallel writers
        # never observe a half-updated link.
        tmp_link = link_path.with_name(
            f".compile_commands.json.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            os.symlink(target_path, tmp_link)
            os.replace(tmp_link, link_path)
        except OSError as e:
            logger.warning(
                "Could not create compile_commands.json symlink at project root: %s",
                e,
            )
            try:
                os.unlink(tmp_link)
            except OSError:
                pass

    def _collect_compile_commands(
        self, target: Target, project: Project
    ) -> list[dict[str, Any]]:
        """Collect compile commands from a target."""
        commands: list[dict[str, Any]] = []

        # Check intermediate_nodes for compilation commands
        nodes_to_check = list(target.intermediate_nodes)

        for node in nodes_to_check:
            if not isinstance(node, FileNode):
                continue

            build_info = getattr(node, "_build_info", None)
            if build_info is None:
                continue

            # Only include compilation commands (not linking)
            language = build_info.get("language")
            if language not in self.COMPILE_LANGUAGES:
                continue

            # Skip link commands (progcmd, sharedcmd, libcmd)
            command_var = build_info.get("command_var", "")
            if command_var in ("progcmd", "sharedcmd", "libcmd"):
                continue

            sources: list[Any] = build_info.get("sources", [])
            for source in sources:
                if isinstance(source, FileNode):
                    entry = self._make_entry(source, node, build_info, project)
                    if entry:
                        commands.append(entry)

        return commands

    def _make_entry(
        self,
        source: FileNode,
        output: FileNode,
        build_info: dict[str, object],
        project: Project,
    ) -> dict[str, Any] | None:
        """Create a compile_commands.json entry for a source file."""
        tool_name = str(build_info.get("tool", ""))
        command_var = str(build_info.get("command_var", ""))

        # Format the command with effective flags
        command = self._format_command(
            tool_name,
            command_var,
            source,
            output,
            project,
            build_info,
        )

        return {
            "directory": str(project.root_dir.absolute()),
            "file": str(source.path),
            "command": command,
            "output": str(output.path),
        }

    def _format_command(
        self,
        tool_name: str,
        command_var: str,
        source: FileNode,
        output: FileNode,
        project: Project,
        build_info: dict[str, object] | None = None,
    ) -> str:
        """Format the command for a source file.

        Prefers the resolver's pre-expanded tokens in
        ``build_info["command"]`` so the emitted command matches the real
        toolchain call (e.g. MSVC's ``/c /Fo<out>``); hand-assembles
        GCC-style flags only when those tokens are absent (hand-built
        ``_build_info`` in tests that skip ``project.resolve()``).
        """
        command_tokens = build_info.get("command") if build_info else None
        if isinstance(command_tokens, list):
            from pcons.core.subst import to_shell_command

            all_sources = build_info.get("sources") if build_info else None
            expanded = self._expand_command_tokens(
                command_tokens,
                source,
                output,
                project,
                all_sources=cast("list[FileNode]", all_sources)
                if isinstance(all_sources, list)
                else None,
            )
            return to_shell_command(expanded, shell="bash")

        return self._format_command_fallback(
            tool_name, command_var, source, output, build_info
        )

    def _expand_command_tokens(
        self,
        tokens: list[Any],
        source: FileNode,
        output: FileNode,
        project: Project,
        all_sources: list[FileNode] | None = None,
    ) -> list[str]:
        """Expand SourcePath/TargetPath markers and PathToken paths to literals.

        Entries run with ``directory`` = project root (not the build dir),
        so project-relative paths pass through and ``"build"``-typed paths
        get build_dir prepended. For grouped (whole-module) compiles, a bare
        SourcePath expands to all of ``all_sources`` — each per-file entry
        repeats the whole command, the sourcekit-lsp/CMake Swift convention.
        """
        from pcons.core.subst import PathToken, SourcePath, TargetPath

        result: list[str] = []
        for token in tokens:
            if isinstance(token, SourcePath):
                if all_sources and len(all_sources) > 1 and token.index is None:
                    for s in all_sources:
                        result.append(f"{token.prefix}{s.path}{token.suffix}")
                    continue
                result.append(f"{token.prefix}{source.path}{token.suffix}")
            elif isinstance(token, TargetPath):
                result.append(f"{token.prefix}{output.path}{token.suffix}")
            elif isinstance(token, PathToken):
                if token.path_type == "build":
                    path = str(Path(project.build_dir) / token.path)
                    result.append(f"{token.prefix}{path}{token.suffix}")
                else:
                    result.append(token.relativize(lambda p: p))
            else:
                result.append(str(token))
        return result

    def _format_command_fallback(
        self,
        tool_name: str,
        command_var: str,
        source: FileNode,
        output: FileNode,
        build_info: dict[str, object] | None = None,
    ) -> str:
        """Hand-assemble a shell command when build_info has no pre-expanded
        tokens, including effective flags from the context if available."""
        import shlex

        tool_cmd = tool_name
        flags = []
        if build_info:
            env = build_info.get("env")
            if env is not None:
                tool_config = getattr(env, tool_name, None)
                if tool_config is not None:
                    cmd = getattr(tool_config, "cmd", None)
                    if cmd:
                        tool_cmd = str(cmd)
                    if hasattr(tool_config, "flags"):
                        flags.extend(str(f) for f in tool_config.flags)

        # Fallback to generic names if no env available
        if tool_cmd == "cc":
            tool_cmd = "cc"
        elif tool_cmd == "cxx":
            tool_cmd = "c++"

        parts: list[str] = [tool_cmd, "-c", *flags]

        # Effective requirements from context
        if build_info:
            context = build_info.get("context")
            if context is not None and isinstance(context, CompileLinkContext):
                for inc in context.includes:
                    parts.append(f"{context.include_prefix}{inc}")
                for define in context.defines:
                    parts.append(f"{context.define_prefix}{define}")
                # PathToken flags fall back to their plain string form; the
                # compile_commands generator does not relativize like ninja.
                parts.extend(str(f) for f in context.flags)

        parts.extend(["-o", str(output.path), str(source.path)])

        return " ".join(shlex.quote(p) for p in parts)
