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
from typing import TYPE_CHECKING, Any

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
    clang tools, IDEs, and language servers.

    Format:
        [
            {
                "directory": "/path/to/project",
                "file": "src/main.cpp",
                "command": "clang++ -c -o build/main.o src/main.cpp",
                "output": "build/main.o"
            },
            ...
        ]

    Example:
        generator = CompileCommandsGenerator()
        generator.generate(project)
        # Creates <build_dir>/compile_commands.json
    """

    # Languages that should be included in compile_commands.json
    COMPILE_LANGUAGES = {"c", "cxx", "cpp", "objc", "objcxx", "cuda"}

    def __init__(self) -> None:
        super().__init__("compile_commands")

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Generate compile_commands.json.

        Args:
            project: Configured project to generate for.
            output_dir: Directory to write compile_commands.json to.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "compile_commands.json"

        commands: list[dict[str, Any]] = []

        for target in project.targets:
            commands.extend(self._collect_compile_commands(target, project))

        with open(output_file, "w") as f:
            json.dump(commands, f, indent=2)
            f.write("\n")

        self._create_root_symlink(output_file, project)

    def _create_root_symlink(self, output_file: Path, project: Project) -> None:
        """Create a symlink to compile_commands.json in the project root.

        This allows IDEs and tools like clangd to find the file at the
        project root without configuration. If the symlink cannot be
        created (e.g., on Windows without privileges), a warning is logged.

        The link is created atomically (a uniquely-named temp symlink swapped
        into place with os.replace), so concurrent generate() runs — parallel
        test workers, or simultaneous pcons invocations — cannot race on it.

        Args:
            output_file: Path to the generated compile_commands.json.
            project: The project (used for root_dir).
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

        Includes effective flags from build_info context if available.
        The command is formatted as a shell command string with proper quoting.
        """
        import shlex

        # Get the actual compiler command from the environment
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
                        # Collect flags
                        flags.extend(str(f) for f in tool_config.flags)

        # Fallback to generic names if no env available
        if tool_cmd == "cc":
            tool_cmd = "cc"
        elif tool_cmd == "cxx":
            tool_cmd = "c++"

        parts: list[str] = [tool_cmd, "-c", *flags]

        # Add effective requirements from context
        if build_info:
            context = build_info.get("context")
            if context is not None and isinstance(context, CompileLinkContext):
                # Format flags using context's prefix attributes
                for inc in context.includes:
                    parts.append(f"{context.include_prefix}{inc}")
                for define in context.defines:
                    parts.append(f"{context.define_prefix}{define}")
                parts.extend(context.flags)

        parts.extend(["-o", str(output.path), str(source.path)])

        # Quote each part for shell, then join
        return " ".join(shlex.quote(p) for p in parts)
