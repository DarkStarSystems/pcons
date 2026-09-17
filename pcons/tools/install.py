# SPDX-License-Identifier: MIT
"""Install tool (copy command templates) and the Install/InstallAs/InstallDir/
OverlayDir builders.

Users can customize the copy commands via the tool namespace
(env.install.copycmd) or override destdir per InstallDir target.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.builder import anchor_target_path, anchor_target_paths
from pcons.core.builder_registry import builder
from pcons.core.node import BuildInfo, FileNode, PathRole
from pcons.core.resolver import PendingSourceFactory
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.core.target import Target
from pcons.tools.tool import StandaloneTool
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.project import Project
    from pcons.util.source_location import SourceLocation


@dataclass
class InstallContext:
    """Context for install operations (copy, copytree).

    Attributes:
        destdir: Destination directory for InstallDir operations.
        install_type: Type of install ("copy" or "copytree").
    """

    destdir: str = ""
    install_type: str = "copy"

    def get_env_overrides(self) -> dict[str, str]:
        """Return values to set on env.install.* before subst()."""
        result: dict[str, str] = {}

        if self.destdir:
            result["destdir"] = self.destdir

        return result

    @classmethod
    def from_target(
        cls, target: Target, env: Environment | None = None, destdir: str = ""
    ) -> InstallContext:
        """Create an InstallContext from a target and optional environment.

        Target settings take precedence over environment settings.

        Args:
            target: The install target being built.
            env: Optional environment with install defaults.
            destdir: Destination directory (for InstallDir).
        """
        effective_destdir = destdir

        builder_name = getattr(target, "_builder_name", "Install")
        install_type = "copytree" if builder_name == "InstallDir" else "copy"

        if env is not None:
            install_config = getattr(env, "install", None)
            if install_config is not None:
                env_destdir = getattr(install_config, "destdir", None)
                if env_destdir is not None and not effective_destdir:
                    effective_destdir = str(env_destdir)

        target_destdir = getattr(target, "_install_destdir", None)
        if target_destdir is not None:
            effective_destdir = target_destdir

        return cls(
            destdir=effective_destdir,
            install_type=install_type,
        )


def _stamp_name_for(path: Path | str) -> str:
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


#: Characters that would be confusing or illegal in a target name. Dots are
#: kept: today's labels already carry them (install_icon.png).
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._+-]")


def _dest_suffix(project: Project, dest: Path) -> str:
    """Flatten a destination path into a target-label suffix.

    The label reaches a reader, in ``pcons info --targets`` and every
    diagnostic, so it says which install this is. The destination's
    *basename* alone does not: 279 plugins each installing into
    ``<name>.bundle/Contents/MacOS`` would all read ``install_MacOS``. The
    whole path tells them apart.

    Canonicalized (root-relative when under the project root) so the label
    doesn't embed an absolute path, and computed *before* the install prefix
    is applied, so PCONS_INSTALL_PREFIX can't leak into it and make
    build.ninja vary between runs.
    """
    canonical = project.top_path_resolver.canonicalize(dest)
    parts = canonical.parts[1:] if canonical.anchor else canonical.parts
    return "_".join(_UNSAFE_IN_NAME.sub("_", part) for part in parts)


def _install_target_name(project: Project, dest: Path, prefix: str) -> str:
    """``<prefix>_<flattened dest>``, or just *prefix* for the "." destination."""
    suffix = _dest_suffix(project, dest)
    return f"{prefix}_{suffix}" if suffix else prefix


def _apply_install_prefix(project: Project, dest: Path, no_prefix: bool) -> Path:
    """Prepend PCONS_INSTALL_PREFIX to *dest* unless it is rooted or opted out."""
    if no_prefix or _is_rooted(dest):
        return dest
    from pcons import get_var

    prefix = get_var("PCONS_INSTALL_PREFIX", project.root_dir / "dist")
    return prefix / dest


def _exclude_flags(exclude: Sequence[str]) -> dict[str, list[str]]:
    """``--exclude`` tokens for the overlay command, one per pattern.

    ``--exclude=PATTERN`` rather than two tokens: extra command flags are
    appended one at a time and dropped when already present, so a repeated
    bare ``--exclude`` would swallow every pattern after the first.
    """
    flags = [f"--exclude={pattern}" for pattern in exclude]
    return {"extra_command_flags": flags} if flags else {}


def _mode_flags(target: Target) -> dict[str, list[str]]:
    """``--mode`` tokens for the copy command, when a mode was asked for.

    They join the command text, so installs sharing a mode share a rule and
    the two or three distinct modes in a project get one rule each.
    """
    mode = target._builder_data.get("mode")
    return {"extra_command_flags": ["--mode", str(mode)]} if mode else {}


def _with_mode(data: dict[str, str], mode: int | None) -> dict[str, str]:
    """Record an explicit install mode, octal, for the copy command."""
    if mode is not None:
        data["mode"] = format(mode, "o")
    return data


def _make_install_target(
    project: Project,
    target_name: str,
    builder_name: str,
    builder_data: dict[str, Any],
    sources: Sequence[Target | Node | Path | str],
    *,
    env: Environment | None = None,
    defined_at: SourceLocation,
) -> Target:
    """Create an interface Target carrying install builder metadata.

    The target is anonymous: its name is a label read off the destination
    (or the call's ``name=``), so several installs may wear one.

    *env*, when the caller named one, is the environment the destination is
    anchored under and the copy command comes from. It is set after
    construction, so the target's registered name stays the one asked for.
    """
    install_target = Target(
        target_name,
        target_type="interface",
        defined_at=defined_at,
        project=project,
        anonymous=True,
    )
    if env is not None:
        builder_data["env"] = env
        install_target._env = env
    install_target._builder_name = builder_name
    # An install operates on products, so it is a step: `ninja all`, an
    # alias, or its name (see pcons.core.tiers).
    install_target.place_in_tier("all", by=builder_name)
    install_target._builder_data = builder_data
    install_target._add_pending_sources(sources)
    return install_target


def install_dir(env: Environment, target_type: str) -> str:
    """Return the conventional install subdirectory for *target_type*.

    The convention is sourced from the environment's primary toolchain, asked
    about ``env.target``, so it follows the platform being built for rather
    than the build machine:

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
        env: Environment whose toolchain defines the convention and whose
            ``target`` says which platform it is being asked about.
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
    return toolchains[0].get_install_dir(target_type, env.target)


class InstallTool(StandaloneTool):
    """Tool for file and directory installation operations.

    Provides cross-platform copy commands using Python helpers.
    The Install, InstallAs, InstallDir and OverlayDir builders reference
    these command templates.

    Variables:
        copycmd: Command template for single file copy (list of tokens).
                 Default: [python, -m, pcons.util.commands, copy, $$SOURCE, $$TARGET]
        copytreecmd: Command template for directory tree copy (list of tokens).
                     Default: [python, -m, pcons.util.commands, copytree, ...]
        overlaycmd: Command template for the OverlayDir merge (list of tokens).
                    Default: [python, -m, pcons.util.commands, overlay, ...]
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
            "overlaycmd": [
                python_cmd,
                "-m",
                "pcons.util.commands",
                "overlay",
                "--depfile",
                TargetPath(suffix=".d"),
                "--stamp",
                TargetPath(),
                "$install.destdir",
                SourcePath(),
            ],
            "destdir": "",
        }

    def builders(self) -> dict[str, Builder]:
        """Empty: builders are registered via the @builder decorator below."""
        return {}


class InstallNodeFactory(PendingSourceFactory):
    """Factory creating install/copy nodes from a target's resolved sources."""

    def resolve_pending(self, target: Target) -> None:
        """Create the install nodes.

        The source targets have resolved by now, so an install target can
        reference their outputs.
        """
        if not target._builder_data:
            return

        builder_name = target._builder_name
        if builder_name not in ("Install", "InstallAs", "InstallDir", "OverlayDir"):
            return

        resolved_sources = self._resolve_sources(target)
        key = "dest" if builder_name == "InstallAs" else "dest_dir"
        dest = Path(target._builder_data[key])
        # OverlayDir takes an environment, so its destination was anchored
        # against that environment when the target was declared.
        if builder_name != "OverlayDir":
            dest = self._anchor_dest(target, dest)

        if builder_name == "Install":
            self._create_install_nodes(target, resolved_sources, dest)
        elif builder_name == "InstallAs":
            self._create_install_as_node(target, resolved_sources, dest)
        elif builder_name == "InstallDir":
            self._create_install_dir_node(target, resolved_sources, dest)
        else:
            exclude = cast("Sequence[str]", target._builder_data.get("exclude", ()))
            self._create_overlay_nodes(target, resolved_sources, dest, exclude)

    def _anchor_dest(self, target: Target, dest: Path) -> Path:
        """Anchor an install destination the way every builder's targets are.

        A destination is a target path, so it goes through the one rule in
        :func:`~pcons.core.builder.anchor_target_path`: it carries the
        declaring script's offset, absorbs a written-out build directory
        prefix once, and passes through when it is rooted (the ordinary
        install, outside the build tree).

        The anchor is the environment's build directory when the caller
        named one, ``build_prefix`` and all, and the project's plain build
        directory otherwise: a target that merely inherited an environment
        did not choose it, so that one's prefix is nobody's intent.
        """
        resolver = self.project.top_path_resolver
        env = cast("Environment | None", target._builder_data.get("env"))
        build_dir = (
            env.build_dir_for(target._subdir)
            if env is not None
            else resolver.build_dir / target._subdir
        )
        return anchor_target_path(resolver, build_dir, dest, target_name=target.name)

    def _destdir(self, dest: Path) -> str:
        """*dest* as the copy command sees it.

        The command runs in the top-level build directory whichever
        subdirectory declared the install, so the offset an anchored
        destination carries belongs in the argument too.
        """
        return self.project.top_path_resolver.make_execution_relative(dest)

    def _install_role(self, dest: Path) -> PathRole | None:
        """The node role for an anchored install destination.

        A destination outside the build tree is an ``"install_output"``:
        the generators name it from the project root, not from the build
        directory they run in. Anything anchoring put inside the build tree
        is an ordinary build output, staging directories included (e.g. the
        ``no_prefix`` installers in ``pcons.contrib.installers``).
        """
        return "install_output" if Path(self._destdir(dest)).anchor else None

    def _get_install_env(self, target: Target) -> Environment | None:
        """Get the target's env, or any project env with the install tool."""
        env = target._builder_data.get("env") or getattr(target, "_env", None)
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
            dest_node = self.project.node(dest_path, role=self._install_role(dest_path))
            dest_node.add_inputs([file_node])

            dest_node._build_info = {
                "tool": "install",
                "command_var": "copycmd",
                "sources": [file_node],
                "description": "INSTALL $out",
                "env": env,
                **_mode_flags(target),
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
        source_path = source_node.path
        dest_path = dest_dir / source_path.name

        # Dest as the command sees it, which is also a platform-neutral stamp name
        rel_dest = self._destdir(dest_path)

        stamps_dir = target.build_dir / ".stamps"
        stamp_name = _stamp_name_for(rel_dest)
        stamp_path = stamps_dir / stamp_name

        stamp_node = self.project.node(stamp_path)
        # Source directory is the explicit dep (becomes $in for copytree).
        # Child nodes are implicit deps — they trigger rebuilds but don't
        # appear in $in (ninja's | syntax).
        stamp_node.add_inputs([source_node])
        child_nodes = self.project.get_child_nodes(source_path)
        stamp_node.implicit_deps.extend(child_nodes)

        context = InstallContext.from_target(target, env, destdir=rel_dest)

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

    def _create_overlay_nodes(
        self,
        target: Target,
        sources: list[FileNode],
        dest_dir: Path,
        exclude: Sequence[str],
    ) -> None:
        """Create the one stamp node an OverlayDir target builds.

        The merged set is not enumerated here. Which files win is decided by
        the overlay command when it runs, so a file another edge generates
        into a source tree is staged by the build that writes it.
        """
        rel_dest = self._destdir(dest_dir)

        stamp_path = target.build_dir / ".stamps" / _stamp_name_for(rel_dest)
        stamp_node = self.project.node(stamp_path)
        stamp_node.add_inputs(sources)

        env = self._get_install_env(target)
        context = InstallContext.from_target(target, env, destdir=rel_dest)

        stamp_node._build_info = cast(
            BuildInfo,
            {
                "tool": "install",
                "command_var": "overlaycmd",
                "sources": list(sources),
                "depfile": PathToken(
                    path=str(stamp_path), path_type="build", suffix=".d"
                ),
                "deps_style": "gcc",
                "restat": True,
                "description": "OVERLAY $out",
                "context": context,
                "env": env,
                **_exclude_flags(exclude),
            },
        )

        target._install_nodes = [stamp_node]
        target.output_nodes.append(stamp_node)

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

        source_node = sources[0]

        # Via project.node() for deduplication; install_output role only
        # for outside-build destinations (see _install_role).
        dest_node = self.project.node(dest, role=self._install_role(dest))
        dest_node.add_inputs([source_node])

        env = self._get_install_env(target)
        dest_node._build_info = {
            "tool": "install",
            "command_var": "copycmd",
            "sources": [source_node],
            "description": "INSTALL $out",
            "env": env,
            **_mode_flags(target),
        }

        target._install_nodes = [dest_node]
        target.output_nodes.append(dest_node)

    def _create_install_dir_node(
        self, target: Target, sources: list[FileNode], dest_dir: Path
    ) -> None:
        """Create copytree node for InstallDir target."""
        if not sources:
            return

        if len(sources) > 1:
            from pcons.core.errors import BuilderError

            raise BuilderError(
                f"InstallDir expects exactly one source directory, got {len(sources)}.",
                location=target.defined_at,
            )

        source_node = sources[0]
        source_path = source_node.path

        dest_path = dest_dir / source_path.name

        # Dest as the command sees it, which is also a platform-neutral stamp name
        rel_dest = self._destdir(dest_path)

        stamps_dir = target.build_dir / ".stamps"
        stamp_name = _stamp_name_for(rel_dest)
        stamp_path = stamps_dir / stamp_name

        # The stamp under build/.stamps is what ninja tracks; the copied
        # tree's destination is passed via the copytree command's destdir.
        stamp_node = self.project.node(stamp_path)
        # Source directory is the explicit dep (becomes $in for copytree).
        # Child nodes are implicit deps — they trigger rebuilds but don't
        # appear in $in (ninja's | syntax).
        stamp_node.add_inputs([source_node])
        child_nodes = self.project.get_child_nodes(source_path)
        stamp_node.implicit_deps.extend(child_nodes)

        env = self._get_install_env(target)
        context = InstallContext.from_target(target, env, destdir=rel_dest)

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


@builder(
    "Install",
    target_type="interface",
    build_tier="all",
    factory_class=InstallNodeFactory,
)
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
        env: Environment | None = None,
        name: str | None = None,
        no_prefix: bool = False,
        mode: int | None = None,
    ) -> Target:
        """Create an Install target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory path.
            sources: Files to install.
            env: Environment whose build directory the destination is
                anchored under, and whose install tool provides the copy
                command. Without one the destination is anchored under the
                project's build directory, which is what a plain
                ``project.Install("lib", ...)`` wants; name an environment
                when the destination has to follow its ``build_prefix``.
            name: Optional label for this target, shown by
                ``pcons info --targets``. Not a name to build by:
                use ``project.Alias()`` for that.
            no_prefix: If True, do not prepend the install prefix to the destination.
            mode: Permissions for the installed copy, e.g. ``0o755``. The copy
                otherwise carries the source's, which is usually right — this
                is for a file that has to arrive more (or less) permissive
                than it sits in the tree.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = name or _install_target_name(project, dest_dir, "install")
        dest_dir = _apply_install_prefix(project, dest_dir, no_prefix)

        return _make_install_target(
            project,
            target_name,
            "Install",
            _with_mode({"dest_dir": str(dest_dir)}, mode),
            list(sources),
            env=env,
            defined_at=get_caller_location(),
        )


@builder(
    "InstallAs",
    target_type="interface",
    build_tier="all",
    factory_class=InstallNodeFactory,
)
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
        env: Environment | None = None,
        name: str | None = None,
        no_prefix: bool = False,
        mode: int | None = None,
    ) -> Target:
        """Create an InstallAs target.

        Args:
            project: The project to add the target to.
            dest: Full destination path (including filename).
            source: Source file.
            env: Environment whose build directory the destination is
                anchored under, and whose install tool provides the copy
                command. Without one the destination is anchored under the
                project's build directory, which is what a plain
                ``project.Install("lib", ...)`` wants; name an environment
                when the destination has to follow its ``build_prefix``.
            name: Optional label for this target, shown by
                ``pcons info --targets``. Not a name to build by:
                use ``project.Alias()`` for that.
            no_prefix: If True, do not prepend the install prefix to the destination.
            mode: Permissions for the installed copy, e.g. ``0o755``. The copy
                otherwise carries the source's, which is usually right — this
                is for a file that has to arrive more (or less) permissive
                than it sits in the tree.

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
        # InstallAs names a *file*, so the whole path goes into the label:
        # two files installed into one directory read differently.
        target_name = name or _install_target_name(project, dest, "install")
        dest = _apply_install_prefix(project, dest, no_prefix)

        return _make_install_target(
            project,
            target_name,
            "InstallAs",
            _with_mode({"dest": str(dest)}, mode),
            [source],
            env=env,
            defined_at=get_caller_location(),
        )


@builder(
    "InstallDir",
    target_type="interface",
    build_tier="all",
    factory_class=InstallNodeFactory,
)
class InstallDirBuilder:
    """Install a directory tree to a destination.

    Merges into the destination: files already there and identical are left
    alone, and anything the source doesn't have is left in place. An install
    directory is often shared, so clearing it would take other people's files
    with it. Ninja's depfile mechanism re-runs the copy when a source file
    changes, and only the changed files are written.
    """

    @staticmethod
    def create_target(
        project: Project,
        dest_dir: Path | str,
        source: Target | FileNode | Path | str,
        *,
        env: Environment | None = None,
        name: str | None = None,
        no_prefix: bool = False,
    ) -> Target:
        """Create an InstallDir target.

        Args:
            project: The project to add the target to.
            dest_dir: Destination directory.
            source: Source directory.
            env: Environment whose build directory the destination is
                anchored under, and whose install tool provides the copy
                command. Without one the destination is anchored under the
                project's build directory, which is what a plain
                ``project.Install("lib", ...)`` wants; name an environment
                when the destination has to follow its ``build_prefix``.
            name: Optional label for this target, shown by
                ``pcons info --targets``. Not a name to build by:
                use ``project.Alias()`` for that.
            no_prefix: If True, do not prepend the install prefix to the destination.

        Returns:
            A Target representing the install operation.
        """
        dest_dir = Path(dest_dir)
        target_name = name or _install_target_name(project, dest_dir, "install_dir")
        dest_dir = _apply_install_prefix(project, dest_dir, no_prefix)

        return _make_install_target(
            project,
            target_name,
            "InstallDir",
            {"dest_dir": str(dest_dir)},
            [source],
            env=env,
            defined_at=get_caller_location(),
        )


@builder(
    "OverlayDir",
    target_type="interface",
    build_tier="all",
    factory_class=InstallNodeFactory,
    requires_env=True,
)
class OverlayDirBuilder:
    """Merge several source trees into one directory, later sources winning.

    Each source tree's *contents* land directly in the destination, keeping
    their relative paths, so ``a/tree/x/y.txt`` and ``b/tree/x/z.txt`` both
    arrive under ``<dest>/x/``. The source directory's own name is not
    appended; that is what separates this from :class:`InstallDirBuilder`,
    whose callers rely on the name being appended.

    When two trees hold the same relative path, the later one in *sources*
    wins. Argument order is the only rule, so the call site shows the answer.

    One target owns the destination and stages all of it with a single build
    edge, whose only output is a stamp. Individual staged files are therefore
    not build targets: ``ninja <dest>/x/y.txt`` names nothing, and the tool
    removes a stale copy itself rather than leaving it to ``ninja -t clean``.

    The destination is a staging directory in the build tree, anchored under
    *env*'s build directory, and the install prefix is never applied. This is
    file staging, not an install.

    Which files win is decided when that edge runs, not when pcons runs, so a
    file another edge generates into a source tree is staged by the same build
    that writes it — declare the ordering with ``depends()``. The edge reports
    every directory it walked and every file it copied in a depfile, so adding,
    removing or editing a file anywhere under a source tree restages on the
    next build with no hand-run of pcons: directories catch an add or a
    removal, files catch an edit in place.

    Removing a file from a source tree removes its staged copy, along with any
    directory that leaves empty. Only the files this target staged are
    candidates — the stamp records them — so anything else installed into the
    same destination is left alone.

    *exclude* drops entries from every source tree before they are merged.
    Patterns are globs matched against the path relative to *each source
    root*, never the destination and never an absolute path, because the
    roots are the only thing the caller named. A pattern holding no ``/``
    matches a name at any depth, one holding a ``/`` is anchored at the
    root, and matching is case sensitive everywhere. An excluded directory
    takes its contents with it. Nothing is excluded by default: a staging
    directory holds what the caller said it holds, and a silent filter is
    worse than a visible one.

    Example::

        stage = project.OverlayDir(
            env,
            "stage/app",
            sources=[shared_dir, app_dir],
            exclude=["*.orig", ".git"],
        )
    """

    @staticmethod
    def create_target(
        project: Project,
        env: Environment,
        dest_dir: Path | str,
        sources: Sequence[Path | str | FileNode | Target],
        *,
        name: str | None = None,
        exclude: Sequence[str] = (),
    ) -> Target:
        """Create an OverlayDir target.

        Args:
            project: The project to add the target to.
            env: Environment whose build directory the destination is
                anchored under.
            dest_dir: Destination directory, relative to that build
                directory.
            sources: Source tree roots, in increasing precedence: the last
                one wins a path the others also hold.
            name: Optional label for this target, shown by
                ``pcons info --targets``. Not a name to build by:
                use ``project.Alias()`` for that.
            exclude: Glob patterns dropped from every source tree, matched
                against paths relative to each source root. A pattern
                matching nothing is not an error: source trees legitimately
                differ in what they hold.

        Returns:
            A Target whose one output is the stamp of the staged tree.
        """
        dest_dir = Path(dest_dir)
        target_name = name or _install_target_name(project, dest_dir, "overlay")
        anchored = anchor_target_paths(env, [dest_dir], target_name=target_name)[0]

        target = _make_install_target(
            project,
            target_name,
            "OverlayDir",
            {"dest_dir": str(anchored), "exclude": list(exclude)},
            list(sources),
            defined_at=get_caller_location(),
        )
        target._env = env
        return target
