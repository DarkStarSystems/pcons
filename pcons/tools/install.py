# SPDX-License-Identifier: MIT
"""Install tool (copy command templates) and the Install/InstallAs/InstallDir
builders.

Users can customize the copy commands via the tool namespace
(env.install.copycmd) or override destdir per InstallDir target.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pcons.core.builder_registry import builder
from pcons.core.node import BuildInfo, FileNode, PathRole
from pcons.core.resolver import PendingSourceFactory
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.core.target import Target
from pcons.tools.tool import StandaloneTool
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.project import Project
    from pcons.util.source_location import SourceLocation


def _stamp_name_for(path: Path) -> str:
    """Convert a path to a flat stamp file name.

    POSIX absolute paths start with "/" which becomes "_"; a Windows
    drive colon is replaced so "C:\\..." becomes "_C_..." to match.
    """
    s = str(path)
    if len(s) >= 2 and s[1] == ":":
        s = "_" + s[0] + s[2:]
    return s.replace("/", "_").replace("\\", "_") + ".stamp"


def _is_rooted(dest: Path) -> bool:
    """Return whether *dest* is rooted (has a drive and/or a leading separator).

    ``Path.anchor`` is used rather than ``Path.is_absolute()`` because the
    latter is platform-dependent: ``Path("/opt/x").is_absolute()`` is False on
    Windows (no drive), which would misclassify a rooted POSIX-style path.
    """
    return bool(dest.anchor)


def _install_role(dest: Path) -> PathRole | None:
    """Return the node role for an install destination.

    A rooted destination lives outside the build tree, so it is an
    ``"install_output"``.
    A relative destination is a build-dir-relative staging path
    (e.g. the ``no_prefix`` installers in ``pcons.contrib.installers``),
    for which ``None`` is returned.
    """
    return "install_output" if _is_rooted(dest) else None


def _deduplicate_target_name(project: Project, base_name: str) -> str:
    """Generate a unique target name, appending a numeric suffix if needed."""
    target_name = base_name
    counter = 1
    while project.get_target(target_name, False) is not None:
        target_name = f"{base_name}_{counter}"
        counter += 1
    if target_name != base_name:
        logger.warning(
            "Install target renamed from '%s' to '%s' to avoid conflict",
            base_name,
            target_name,
        )
    return target_name


def _apply_install_prefix(project: Project, dest: Path, no_prefix: bool) -> Path:
    """Prepend PCONS_INSTALL_PREFIX to *dest* unless it is rooted or opted out."""
    if no_prefix or _is_rooted(dest):
        return dest
    from pcons import get_var

    prefix = get_var("PCONS_INSTALL_PREFIX", str(project.root_dir / "dist"))
    return Path(prefix) / dest


def _make_install_target(
    target_name: str,
    builder_name: str,
    builder_data: dict[str, str],
    sources: Sequence[Target | Node | Path | str],
    *,
    defined_at: SourceLocation,
) -> Target:
    """Create an interface Target carrying install builder metadata."""
    install_target = Target(
        target_name,
        target_type="interface",
        defined_at=defined_at,
    )
    install_target._builder_name = builder_name
    install_target._builder_data = builder_data
    install_target._pending_sources = list(sources)
    return install_target


def install_dir(env: Environment, target_type: str) -> str:
    """Return the conventional install subdirectory for *target_type*.

    The convention is sourced from the environment's primary toolchain, so it
    follows the platform that toolchain targets rather than the host OS:

    - ``"program"``: ``bin``
    - ``"static_library"``: ``lib``
    - ``"shared_library"``: ``bin`` on DLL platforms (a Windows DLL must sit
      next to the executable that loads it), ``lib`` elsewhere.

    Pass the result to :meth:`Project.Install` as the destination directory::

        env = project.Environment(toolchain=find_c_toolchain())
        lib = project.SharedLibrary("foo", env, sources=["foo.c"])
        project.Install(install_dir(env, "shared_library"), [lib])

    Users who want a different layout can ignore this helper and pass an
    explicit directory string (e.g. ``project.Install("lib64", [lib])``).

    Args:
        env: Environment whose toolchain defines the convention.
        target_type: One of ``"program"``, ``"static_library"``,
            ``"shared_library"``.

    Returns:
        The install subdirectory name (relative to the install prefix).

    Raises:
        ValueError: If *env* has no toolchain.
    """
    toolchains = env.toolchains
    if not toolchains:
        raise ValueError(
            "install_dir() requires an environment with a toolchain; "
            "pass an explicit directory string to Install() instead."
        )
    return toolchains[0].get_install_dir(target_type)


class InstallTool(StandaloneTool):
    """Tool for file and directory installation operations.

    Provides cross-platform copy commands using Python helpers.
    The Install, InstallAs, and InstallDir builders reference these
    command templates.

    Variables:
        copycmd: Command template for single file copy (list of tokens).
                 Default: [python, -m, pcons.util.commands, copy, $$SOURCE, $$TARGET]
        copytreecmd: Command template for directory tree copy (list of tokens).
                     Default: [python, -m, pcons.util.commands, copytree, ...]
        destdir: Default destination directory for InstallDir.

    Example:
        # Use system copy on Unix (as list)
        env.install.copycmd = ["cp", "$$SOURCE", "$$TARGET"]

        # Use rsync for directory copies
        env.install.copytreecmd = ["rsync", "-a", "$$SOURCE", "$destdir"]
    """

    def __init__(self) -> None:
        super().__init__("install")

    def default_vars(self) -> dict[str, object]:
        """Return default command templates (cross-platform Python helpers)."""
        python_cmd = sys.executable.replace("\\", "/")
        return {
            "copycmd": [
                python_cmd,
                "-m",
                "pcons.util.commands",
                "copy",
                SourcePath(),
                TargetPath(),
            ],
            # Directory tree copy with depfile support
            "copytreecmd": [
                python_cmd,
                "-m",
                "pcons.util.commands",
                "copytree",
                "--depfile",
                TargetPath(suffix=".d"),
                "--stamp",
                TargetPath(),
                SourcePath(),
                "$install.destdir",
            ],
            "destdir": "",
        }

    def builders(self) -> dict[str, Builder]:
        """Empty: builders are registered via the @builder decorator below."""
        return {}


class InstallNodeFactory(PendingSourceFactory):
    """Factory creating install/copy nodes during pending-sources resolution."""

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources for an install target (phase 2).

        Runs after main resolution when output_nodes are populated, so
        Install targets can reference outputs from other targets.
        """
        if not target._builder_data:
            return

        builder_name = target._builder_name
        if builder_name not in ("Install", "InstallAs", "InstallDir"):
            return

        resolved_sources = self._resolve_sources(target)

        if builder_name == "Install":
            dest_dir = Path(target._builder_data["dest_dir"])
            self._create_install_nodes(target, resolved_sources, dest_dir)
        elif builder_name == "InstallAs":
            dest = Path(target._builder_data["dest"])
            self._create_install_as_node(target, resolved_sources, dest)
        elif builder_name == "InstallDir":
            dest_dir = Path(target._builder_data["dest_dir"])
            self._create_install_dir_node(target, resolved_sources, dest_dir)

    def _get_install_env(self, target: Target) -> Environment | None:
        """Get the target's env, or any project env with the install tool."""
        env = getattr(target, "_env", None)
        if env is not None:
            return env

        for e in self.project.environments:
            if hasattr(e, "install"):
                return e

        return None

    def _create_install_nodes(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copy nodes for Install target.

        Directory sources (those with child nodes in the project graph)
        use copytreecmd (depfile + stamp); file sources use copycmd.
        """
        path_resolver = target.path_resolver
        dest_dir = path_resolver.normalize_target_path(
            dest_dir, target_name=target.name
        )

        env = self._get_install_env(target)

        installed_nodes: list[FileNode] = []
        for file_node in sources:
            if not isinstance(file_node, FileNode):
                continue

            if self.project.has_child_nodes(file_node.path):
                self._create_install_dir_node_for(
                    target, file_node, dest_dir, env, installed_nodes
                )
                continue

            dest_path = dest_dir / file_node.path.name

            # Via project.node() for deduplication; install_output role
            # only for outside-build destinations (see _install_role).
            dest_node = self.project.node(dest_path, role=_install_role(dest_path))
            dest_node.depends([file_node])

            dest_node._build_info = {
                "tool": "install",
                "command_var": "copycmd",
                "sources": [file_node],
                "description": "INSTALL $out",
                "env": env,
            }

            installed_nodes.append(dest_node)

        target._install_nodes = installed_nodes
        target.output_nodes.extend(installed_nodes)

    def _create_install_dir_node_for(
        self,
        target: Target,
        source_node: FileNode,
        dest_dir: Path,
        env: Environment | None,
        installed_nodes: list[FileNode],
    ) -> None:
        """Create a copytree node for a directory source within Install.

        Same copytreecmd + depfile/stamp mechanism as InstallDir.
        """
        from pcons.tools.archive_context import InstallContext

        source_path = source_node.path
        dest_path = dest_dir / source_path.name

        # Dest relative to build dir for a platform-neutral stamp name
        try:
            rel_dest = dest_path.relative_to(target.build_dir)
        except ValueError:
            rel_dest = dest_path

        stamps_dir = target.build_dir / ".stamps"
        stamp_name = _stamp_name_for(rel_dest)
        stamp_path = stamps_dir / stamp_name

        stamp_node = self.project.node(stamp_path)
        # Source directory is the explicit dep (becomes $in for copytree).
        # Child nodes are implicit deps — they trigger rebuilds but don't
        # appear in $in (ninja's | syntax).
        stamp_node.depends([source_node])
        child_nodes = self.project.get_child_nodes(source_path)
        stamp_node.implicit_deps.extend(child_nodes)

        context = InstallContext.from_target(
            target, env, destdir=str(rel_dest).replace("\\", "/")
        )

        stamp_node._build_info = cast(
            BuildInfo,
            {
                "tool": "install",
                "command_var": "copytreecmd",
                "sources": [source_node],
                "depfile": PathToken(
                    path=str(stamp_path), path_type="build", suffix=".d"
                ),
                "deps_style": "gcc",
                "description": "INSTALLDIR $out",
                "context": context,
                "env": env,
            },
        )

        installed_nodes.append(stamp_node)

    def _create_install_as_node(
        self, target: Target, sources: list[FileNode], dest: Path
    ) -> None:
        """Create copy node for InstallAs target."""
        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallAs expects exactly one source, got {len(sources)}. "
                f"Use Install() for multiple files.",
                location=target.defined_at,
            )

        path_resolver = target.path_resolver
        dest = path_resolver.normalize_target_path(dest, target_name=target.name)

        source_node = sources[0]

        # Via project.node() for deduplication; install_output role only
        # for outside-build destinations (see _install_role).
        dest_node = self.project.node(dest, role=_install_role(dest))
        dest_node.depends([source_node])

        env = self._get_install_env(target)
        dest_node._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [source_node],
            "description": "INSTALL $out",
            "env": env,
        }

        target._install_nodes = [dest_node]
        target.output_nodes.append(dest_node)

    def _create_install_dir_node(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copytree node for InstallDir target."""
        from pcons.tools.archive_context import InstallContext

        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallDir expects exactly one source directory, got {len(sources)}.",
                location=target.defined_at,
            )

        path_resolver = target.path_resolver
        dest_dir = path_resolver.normalize_target_path(
            dest_dir, target_name=target.name
        )

        source_node = sources[0]
        source_path = source_node.path

        dest_path = dest_dir / source_path.name

        # Dest relative to build dir for a platform-neutral stamp name
        try:
            rel_dest = dest_path.relative_to(target.build_dir)
        except ValueError:
            rel_dest = dest_path

        stamps_dir = target.build_dir / ".stamps"
        stamp_name = _stamp_name_for(rel_dest)
        stamp_path = stamps_dir / stamp_name

        # The stamp under build/.stamps is what ninja tracks; the copied
        # tree's destination is passed via the copytree command's destdir.
        stamp_node = self.project.node(stamp_path)
        # Source directory is the explicit dep (becomes $in for copytree).
        # Child nodes are implicit deps — they trigger rebuilds but don't
        # appear in $in (ninja's | syntax).
        stamp_node.depends([source_node])
        child_nodes = self.project.get_child_nodes(source_path)
        stamp_node.implicit_deps.extend(child_nodes)

        env = self._get_install_env(target)
        context = InstallContext.from_target(
            target, env, destdir=str(rel_dest).replace("\\", "/")
        )

        stamp_node._build_info = cast(
            BuildInfo,
            {
                "tool": "install",
                "command_var": "copytreecmd",
                "sources": [source_node],
                "depfile": PathToken(
                    path=str(stamp_path), path_type="build", suffix=".d"
                ),
                "deps_style": "gcc",
                "description": "INSTALLDIR $out",
                # Provides get_env_overrides() for template expansion
                "context": context,
                "env": env,
            },
        )

        target._install_nodes = [stamp_node]
        target.output_nodes.append(stamp_node)


@builder("Install", target_type="interface", factory_class=InstallNodeFactory)
class InstallBuilder:
    """Install files to a destination directory.

    Creates copy operations for each source file to the destination
    directory. The returned target depends on all the installed files.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest_dir: Path | str,
        sources: Sequence[Target | FileNode | Path | str],
        *,
        name: str | None = None,
        no_prefix: bool = False,
    ) -> Target:
        """Create an Install target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory path.
            sources: Files to install.
            name: Optional name for the install target.
            no_prefix: If True, do not prepend the install prefix to the destination.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = _deduplicate_target_name(
            project, name or f"install_{dest_dir.name}"
        )
        dest_dir = _apply_install_prefix(project, dest_dir, no_prefix)

        return _make_install_target(
            target_name,
            "Install",
            {"dest_dir": str(dest_dir)},
            list(sources),
            defined_at=get_caller_location(),
        )


@builder("InstallAs", target_type="interface", factory_class=InstallNodeFactory)
class InstallAsBuilder:
    """Install a file to a specific destination path.

    Unlike Install(), this copies a single file to an exact path,
    allowing rename during installation.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest: Path | str,
        source: Target | FileNode | Path | str,
        *,
        name: str | None = None,
        no_prefix: bool = False,
    ) -> Target:
        """Create an InstallAs target.

        Args:
            project: The project to add the target to.
            dest: Full destination path (including filename).
            source: Source file.
            name: Optional name for the install target.
            no_prefix: If True, do not prepend the install prefix to the destination.

        Returns:
            A Target representing the install operation.

        Raises:
            BuilderError: If source is a list (use Install() for multiple files).
        """
        if isinstance(source, (list, tuple)):
            from pcons.core.errors import BuilderError

            raise BuilderError(
                "InstallAs() takes a single source, not a list. "
                "Use Install() for multiple files.",
                location=get_caller_location(),
            )

        dest = Path(dest)
        target_name = _deduplicate_target_name(project, name or f"install_{dest.name}")
        dest = _apply_install_prefix(project, dest, no_prefix)

        return _make_install_target(
            target_name,
            "InstallAs",
            {"dest": str(dest)},
            [source],
            defined_at=get_caller_location(),
        )


@builder("InstallDir", target_type="interface", factory_class=InstallNodeFactory)
class InstallDirBuilder:
    """Install a directory tree to a destination.

    Recursively copies an entire directory tree. Uses ninja's depfile
    mechanism for incremental rebuilds.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest_dir: Path | str,
        source: Target | FileNode | Path | str,
        *,
        name: str | None = None,
        no_prefix: bool = False,
    ) -> Target:
        """Create an InstallDir target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory.
            source: Source directory.
            name: Optional name for the install target.
            no_prefix: If True, do not prepend the install prefix to the destination.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = _deduplicate_target_name(
            project, name or f"install_dir_{dest_dir.name}"
        )
        dest_dir = _apply_install_prefix(project, dest_dir, no_prefix)

        return _make_install_target(
            target_name,
            "InstallDir",
            {"dest_dir": str(dest_dir)},
            [source],
            defined_at=get_caller_location(),
        )
