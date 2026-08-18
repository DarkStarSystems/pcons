# SPDX-License-Identifier: MIT
"""Project container for pcons builds.

The Project is the top-level container that holds all environments,
targets, and nodes for a build. It provides node deduplication and
serves as the context for build descriptions.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.environment import Environment as Env
from pcons.core.errors import PconsError
from pcons.core.graph import (
    collect_all_nodes,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.invocation import program_name, running_as_a_program
from pcons.core.node import AliasNode, DirNode, FileNode, Node, PathRole
from pcons.core.paths import PathResolver
from pcons.core.target import Target, split_qualified_name
from pcons.util.source_location import SourceLocation, get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons._cli_click import UserCommand, UserGroup
    from pcons.core._project_builder_stubs import _ProjectBuilders
    from pcons.core._toolchain_names import KnownToolchain
    from pcons.tools.toolchain import Toolchain
else:
    # At runtime, builder lookup goes through Project.__getattr__; the
    # mixin's only purpose is to declare typed methods for static analysis.
    _ProjectBuilders = object


class _ChildNodeIndex:
    """Directory -> registry keys of the nodes beneath it.

    Answers ``get_child_nodes()``/``has_child_nodes()`` without scanning the
    whole node registry, which is otherwise O(nodes) per call — install-heavy
    projects (one bundle per plugin, say) make that call once per install
    target and spend all their generate time there.

    Two paths keep it honest:

    * ``Project.node()``/``dir_node()`` *maintain* it as nodes are registered
      — O(path depth) each, and the only path a normal build takes.
    * :meth:`sync` *repairs* it. A build script may write ``Project._nodes``
      directly (``examples/15_custom_builder`` does), so a count mismatch
      means the index missed something and it is rebuilt wholesale. Rare, so
      the rebuild cost doesn't matter; the check itself is an int compare.

    Stores paths rather than Node objects, so replacing the node under an
    existing key can't leave a stale object behind — the caller resolves
    paths through ``_nodes`` at query time.
    """

    __slots__ = ("_by_dir", "_accounted", "_build_dir")

    def __init__(self) -> None:
        self._by_dir: dict[Path, list[Path]] = {}
        self._accounted = 0
        self._build_dir: Path | None = None

    def add(self, key: Path, normalized: Path) -> None:
        """Record *key* under every directory that contains it."""
        for parent in normalized.parents:
            self._by_dir.setdefault(parent, []).append(key)
        self._accounted += 1

    def sync(
        self,
        nodes: dict[Path, Node],
        normalize: Callable[[Path], Path],
        build_dir: Path,
    ) -> None:
        """Rebuild if the registry changed behind our back, or build_dir moved."""
        if self._accounted == len(nodes) and self._build_dir == build_dir:
            return
        self._by_dir = {}
        self._accounted = 0
        self._build_dir = build_dir
        for key in nodes:
            self.add(key, normalize(key))

    def children(self, directory: Path) -> list[Path]:
        """Registry keys strictly beneath *directory*, in registration order."""
        return self._by_dir.get(directory, [])


def _in_virtualenv(path: Path, root: Path) -> bool:
    """Whether *path* lives inside a virtualenv under *root*.

    Identified by ``pyvenv.cfg``, which every venv has at its top, rather than
    by name — ``.venv`` is only a convention.
    """
    for parent in path.parents:
        if not parent.is_relative_to(root):
            return False
        if (parent / "pyvenv.cfg").exists():
            return True
    return False


#: The pending "this build script was run directly" notice: (armed, script).
#: Deferred to interpreter exit because at construction time an embedded
#: driver is indistinguishable from a build script, and a driver that goes
#: on to call write_build_files() deserves no warning. The exit hook only
#: prints — it is not the generation-at-exit hook that issue #84 removed.
_direct_run_notice: dict[str, Any] = {"armed": False, "script": None, "hook": False}


def _schedule_direct_run_notice(script: Path) -> None:
    _direct_run_notice["armed"] = True
    _direct_run_notice["script"] = script
    if not _direct_run_notice["hook"]:
        _direct_run_notice["hook"] = True
        import atexit

        atexit.register(_emit_direct_run_notice)


def _cancel_direct_run_notice() -> None:
    _direct_run_notice["armed"] = False


def _emit_direct_run_notice() -> None:
    if not _direct_run_notice["armed"]:
        return
    _direct_run_notice["armed"] = False
    logger.warning(
        "this build script was run directly, so nothing was "
        "generated.\n"
        "Run it with pcons instead:\n"
        "\n"
        "    pcons -b %s\n"
        "\n"
        "or hand over to the CLI from the top of the script, see\n"
        "https://pcons.readthedocs.io/en/latest/cli/"
        "#a-build-script-that-runs-itself",
        program_name(_direct_run_notice["script"]),
    )


class Project(_ProjectBuilders):
    """Top-level container for a pcons build.

    The Project manages:
    - Environments for different build configurations
    - Targets (libraries, programs, etc.)
    - Node deduplication (same path → same node)
    - Default targets for 'ninja' with no arguments
    - Build validation (cycle detection, missing sources)

    Example:
        project = Project("myproject")

        # Create environment with toolchain
        env = project.Environment(toolchain=gcc)

        # Create targets
        lib = project.Library("mylib", env, sources=["lib.cpp"])
        app = project.Program("app", env, sources=["main.cpp"])
        app.link(lib)

        # Set defaults
        project.Default(app)

    Attributes:
        name: Project name.
        root_dir: Project root directory.
        build_dir: Directory for build outputs.
        config: Cached configuration (from configure phase).
    """

    __slots__ = (
        "name",
        "root_dir",
        "build_dir",
        "_environments",
        "_targets",
        "_nodes",
        "_aliases",
        "_default_targets",
        "_config",
        "_resolved",
        "_path_resolver",
        "_found_packages",
        "_package_finder_chain",
        "_configure_deps",
        "_pending_stages",
        "_child_index",
        "defined_at",
        "_parent",
        "_children",
        "_subdir",
        "_offset",
        "__generated",
        "__weakref__",  # allow weak references (e.g. per-project caches)
    )

    __current: Project | None = None
    __top_level: Project | None = None
    # Projects whose _enter_subdir context is active, innermost last. A
    # Project created while this is non-empty becomes a subproject of the
    # innermost entry; created while it is empty with a top-level project
    # already present, it is an error.
    __parent_stack: list[Project] = []

    @staticmethod
    def _clear_tree() -> None:
        """Clear the project tree (for testing purposes)."""
        Project.__current = None
        Project.__top_level = None
        Project.__parent_stack.clear()
        _cancel_direct_run_notice()

    def __init__(
        self,
        name: str,
        *,
        root_dir: Path | str | None = None,
        build_dir: Path | str | None = None,
        config: Any = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create a project.

        Args:
            name: Project name.
            root_dir: Project root directory. Defaults to the
                PCONS_SOURCE_DIR environment variable if set (the CLI
                sets this automatically), then the directory containing
                the calling script, then the current working directory.
            build_dir: Directory for build outputs. Defaults to the
                PCONS_BUILD_DIR environment variable if set (the CLI
                sets this automatically), otherwise "build". That default
                belongs to the first project: an independent sibling
                project must pass its own. A sub-project's build_dir is
                derived from its parent's, and passing one is ignored.
            config: Cached configuration from configure phase.
            defined_at: Source location where project was created.
        """
        self.name = name
        defined_at = defined_at or get_caller_location()
        if root_dir is None and Project.__top_level is None:
            root_dir = os.environ.get("PCONS_SOURCE_DIR")
        if root_dir is None:
            # Infer from the script that called Project()
            caller = defined_at
            caller_file = Path(caller.filename)
            if caller_file.exists():
                root_dir = str(caller_file.parent)
            else:
                # Stale .pyc files (paths baked in on another machine) or
                # frozen callers land here; the cwd fallback below can put
                # the root somewhere surprising, so leave a trace.
                logger.debug(
                    "Project root inference: caller file %r does not exist; "
                    "falling back to cwd %s (pass root_dir= to be explicit)",
                    caller.filename,
                    Path.cwd(),
                )
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        self._environments: list[Env] = []
        self._targets: list[Target] = []
        self._nodes: dict[Path, Node] = {}
        self._aliases: dict[str, AliasNode] = {}
        self._default_targets: list[Target] = []
        self._config = config
        self._resolved = False
        # None caches a negative find_package result for the key.
        self._found_packages: dict[
            tuple[str, str | None, tuple[str, ...], bool], Target | None
        ] = {}
        self._package_finder_chain: Any = None  # Lazy-initialized FinderChain
        # Files the build description read while running: the generated build
        # files depend on these, so editing one re-runs pcons (see
        # add_configure_dependency / generated_input).
        self._configure_deps: list[Path] = []
        # Staged inputs that did not exist yet, so their blocks were skipped.
        self._pending_stages: list[Path] = []
        # Directory -> node keys beneath it, for get_child_nodes().
        self._child_index = _ChildNodeIndex()
        self.defined_at = defined_at
        self._subdir = None
        # Offset from the top-level project's root to this project's root.
        # Empty for the top-level project; node paths are relative to it.
        self._offset = Path()
        self._children: list[Project] = []
        self.__generated = False

        if Project.__parent_stack:
            self._parent = Project.__parent_stack[-1]
        else:
            self._parent = None

        if self._parent:
            if build_dir is not None:
                import warnings

                warnings.warn(
                    f"Project '{self.name}': build_dir argument is ignored for sub-projects; "
                    f"using parent project's build_dir '{self._parent.build_dir}' instead.",
                    UserWarning,
                    stacklevel=2,
                )
            self._parent._children.append(self)
            # root_dir and build_dir stay this project's own, so a script
            # written to build standalone reads the right directories when it
            # is embedded. The offset from the top-level root is recorded
            # separately; node paths are anchored there (see _offset).
            top = self._parent.top
            try:
                self._offset = self.root_dir.relative_to(top.root_dir)
            except ValueError as exc:
                raise ValueError(
                    f"Sub-project '{self.name}' at {self.root_dir} is not inside "
                    f"the top-level project at {top.root_dir}. add_subdirectory() "
                    f"only works for directories under the top-level project."
                ) from exc
            self.build_dir = top.build_dir / self._offset
        else:
            first = Project.__top_level
            if build_dir is None:
                if first is not None:
                    # An independent sibling. The -B / PCONS_BUILD_DIR
                    # default can only serve one project, and it serves
                    # the first; a sibling names its own build directory.
                    default = os.environ.get("PCONS_BUILD_DIR", "build")
                    raise PconsError(
                        f"project {name!r} needs an explicit build_dir: "
                        f"the default ({default!r}) is reserved for the "
                        f"first project, {first.name!r} "
                        f"({first.defined_at}).\n"
                        "Each top-level project owns its own build "
                        "directory; pass build_dir= to the later ones. To "
                        "build a subdirectory as part of an existing "
                        "project instead, use add_subdirectory().",
                        location=defined_at,
                    )
                build_dir = os.environ.get("PCONS_BUILD_DIR", "build")
            if not self.root_dir.is_absolute():
                raise ValueError(
                    f"Root directory must be absolute (got: {self.root_dir})"
                )
            bd = Path(build_dir)
            if bd.is_absolute():
                try:
                    bd = bd.relative_to(self.root_dir)
                except ValueError:
                    pass  # Out-of-tree build — keep absolute
            self.build_dir = bd

            if first is not None:
                mine = self._effective_output_dir()
                for other in Project._top_level_projects():
                    # normcase: on case-insensitive filesystems, two
                    # spellings of one directory still collide.
                    if os.path.normcase(str(other._effective_output_dir())) == (
                        os.path.normcase(str(mine))
                    ):
                        raise PconsError(
                            f"projects {other.name!r} ({other.defined_at}) "
                            f"and {name!r} would share the build directory "
                            f"{mine}.\n"
                            "Each top-level project owns its own build "
                            "directory; pass a distinct build_dir=.",
                            location=defined_at,
                        )

        # Anchored at the top of this project's tree, which is where node
        # paths are rooted; `path_resolver` narrows it to this project's
        # directory.
        top = self.top
        self._path_resolver = PathResolver(top.root_dir, top.build_dir)

        # Register with the global registry (the CLI's iteration source).
        # After validation, to ensure we only register valid projects.
        from pcons import _register_project

        _register_project(self)

        Project.__current = self
        if Project.__top_level is None:
            script = Path(defined_at.filename)
            if running_as_a_program(script):
                _schedule_direct_run_notice(script)
            Project.__top_level = self

    def _effective_output_dir(self) -> Path:
        """Where this project's build outputs land, as a normalized path.

        Pure path arithmetic (no filesystem access); used to detect two
        top-level projects claiming the same build directory.
        """
        bd = (
            self.build_dir
            if self.build_dir.is_absolute()
            else (self.root_dir / self.build_dir)
        )
        return Path(os.path.normpath(bd))

    @staticmethod
    def _top_level_projects() -> list[Project]:
        """Every registered top-level project, in creation order."""
        from pcons import get_registered_projects

        return [p for p in get_registered_projects() if p.is_top_level]

    @staticmethod
    def has_current() -> bool:
        """Whether a project is currently active (for CLI or add_subdirectory)."""
        return Project.__current is not None

    @staticmethod
    def current() -> Project:
        if Project.__current is None:
            raise ValueError("no project is currently active")
        return Project.__current

    @staticmethod
    def top_level() -> Project:
        if Project.__top_level is None:
            raise ValueError("no top-level project is currently active")
        return Project.__top_level

    @property
    def is_top_level(self) -> bool:
        return self._parent is None

    @property
    def top(self) -> Project:
        """The top-level project of this project's tree."""
        project = self
        while project._parent is not None:
            project = project._parent
        return project

    @property
    def parent(self) -> Project:
        """Get the parent project if this is a subdir, or None if top-level."""
        if self._parent is None:
            raise ValueError("This project has no parent (it is top-level).")
        return self._parent

    @property
    def current_dir(self) -> Path:
        """Get the current directory for this project, taking subdirs into account."""
        if self._subdir:
            return self.root_dir / self._subdir
        return self.root_dir

    @property
    def _node_offset(self) -> Path:
        """Where nodes created here sit relative to the top-level root.

        Node paths are stored relative to the top-level project's root, so
        this is the prefix that turns a path expressed relative to
        ``current_dir`` into that canonical form. It combines this project's
        own offset with any directory entered via ``_enter_subdir``.
        """
        if self._subdir:
            return self._offset / self._subdir
        return self._offset

    @contextmanager
    def _enter_subdir(self, subdir: str | Path) -> Generator[None, None, None]:
        """Context manager for entering a subdirectory in the project."""
        old_subdir = self._subdir
        old_current = Project.__current
        self._subdir = subdir if old_subdir is None else f"{old_subdir}/{subdir}"
        # The entered project is the context: the subdirectory script's
        # Project.current() must mean this tree even when another sibling
        # was created more recently.
        Project.__current = self
        Project.__parent_stack.append(self)
        try:
            yield
        finally:
            Project.__parent_stack.pop()
            self._subdir = old_subdir
            Project.__current = old_current

    def write_build_files(self, *, regen_command: Sequence[str] | None = None) -> None:
        """Write this project's build files, here and now.

        The public drain for embedded use: a program that describes a build
        and calls this gets its build.ninja (or whatever generators were
        selected) immediately, resolved as needed, with no CLI involved.
        Under ``pcons`` it is what happens anyway, so calling it there is
        harmless. With several top-level projects, call it on each.

        Args:
            regen_command: argv for the generated build files'
                self-regeneration rule, used verbatim (the caller owns its
                relocatability — it runs from the build directory). Without
                it, files written from an embedded run get no regen rule:
                ``sys.argv`` names the embedder's program, and re-running
                that as a build step is never right.

        Raises:
            ValueError: ``regen_command`` was passed under a run that
                already owns the regen rule (the CLI), or twice with
                different commands.
        """
        from pcons.core import invocation
        from pcons.generators.generator import BaseGenerator

        if regen_command is not None:
            wanted = [str(part) for part in regen_command]
            recorded = invocation.recorded()
            if recorded is None:
                invocation.record(
                    invocation.Invocation(
                        script=Path(self.defined_at.filename),
                        command_override=wanted,
                    )
                )
            elif recorded.command_override != wanted:
                raise ValueError(
                    "write_build_files(regen_command=...): this run already "
                    "owns the regen rule"
                    + (
                        " (a different regen_command was recorded earlier)"
                        if recorded.command_override is not None
                        else " (the build script is running under pcons)"
                    )
                )
        elif not invocation.run_recorded():
            invocation.suppress_inference()

        _cancel_direct_run_notice()
        BaseGenerator._generate_pending(self)

    def add_subdirectory(
        self, subdir: str | Path, pick: list[str] | None = None
    ) -> Any:
        """Run *subdir*'s pcons-build.py as part of this project.

        This is the :func:`pcons.add_subdirectory` variant, for scripts
        with several top-level projects where "the current project" is
        ambiguous.
        """
        from pcons.util.add_subdirectory import add_subdirectory

        return add_subdirectory(subdir, pick, project=self)

    @property
    def config(self) -> Any:
        """Get the cached configuration."""
        return self._config

    @config.setter
    def config(self, value: Any) -> None:
        """Set the cached configuration."""
        self._config = value

    @property
    def path_resolver(self) -> PathResolver:
        """Get the path resolver for this project's current directory."""
        offset = self._node_offset
        if offset.parts:
            return self._path_resolver.subdir(offset)
        return self._path_resolver

    def Environment(
        self,
        toolchain: Toolchain | KnownToolchain | str | Sequence[str] | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> Env:
        """Create and register a new environment.

        Args:
            toolchain: Optional toolchain to initialize with. A string is
                looked up in the toolchain registry: a finder name like "c"
                auto-detects, a specific alias like "gcc" requires that
                toolchain. A sequence of names is a preference list.
            name: Optional name for this environment (used in ninja rule names).
            **kwargs: Additional variables to set on the environment.

        Returns:
            A new Environment attached to this project.
        """
        env = Env(
            name=name,
            toolchain=toolchain,
            defined_at=get_caller_location(),
        )
        env._project = self

        # Set any extra variables
        for key, value in kwargs.items():
            setattr(env, key, value)

        # Set build_dir from project
        env.build_dir = self.build_dir

        self._environments.append(env)
        return env

    def _canonicalize_path(self, path: Path) -> Path:
        """Convert path to canonical form for node storage.

        Canonical: relative to project root if under it, absolute otherwise.
        Uses pure path arithmetic (no filesystem access).

        Node paths are anchored at the *top-level* root, which is what
        generators resolve them against — not at this project's own root,
        which for a subproject would drop the subproject directory.
        """
        top_root = self.top.root_dir
        if path.is_absolute():
            try:
                return path.relative_to(top_root)
            except ValueError:
                return path  # External path
        return Path(os.path.normpath(path))

    def node(self, path: Path | str, *, role: PathRole | None = None) -> FileNode:
        """Get or create a file node for a path.

        This provides node deduplication - the same path always
        returns the same node instance.

        Args:
            path: Path to the file.
            role: Optional path role recorded on the node (see PathRole).
        """
        path = self._canonicalize_path(Path(path))

        if path not in self._nodes:
            self._nodes[path] = FileNode(
                path, role=role, defined_at=get_caller_location()
            )
            self._child_index.add(path, self._normalize_for_index(path))
        node = self._nodes[path]
        if not isinstance(node, FileNode):
            raise TypeError(
                f"Path {path} is registered as {type(node).__name__}, not FileNode"
            )
        if role is not None:
            node.role = role
        return node

    def dir_node(self, path: Path | str, *, role: PathRole | None = None) -> DirNode:
        """Get or create a directory node for a path (deduplicated)."""
        path = self._canonicalize_path(Path(path))
        if path not in self._nodes:
            self._nodes[path] = DirNode(
                path, role=role, defined_at=get_caller_location()
            )
            self._child_index.add(path, self._normalize_for_index(path))
        node = self._nodes[path]
        if not isinstance(node, DirNode):
            raise TypeError(
                f"Path {path} is registered as {type(node).__name__}, not DirNode"
            )
        if role is not None:
            node.role = role
        return node

    def _add_target(self, target: Target) -> None:
        """Register a target; called only by Target.__init__.

        Raises:
            ValueError: If a target with the same name already exists.
        """
        if (
            existing := self.get_target(
                target.name, raise_if_missing=False, recursive=False
            )
        ) is not None:
            raise ValueError(
                f"Target '{target.name}' already exists "
                f"(defined at {existing.defined_at})"
            )
        self._targets.append(target)

    @overload
    def get_target(
        self, name: str, raise_if_missing: Literal[True] = ..., recursive: bool = ...
    ) -> Target: ...

    @overload
    def get_target(
        self, name: str, raise_if_missing: Literal[False], recursive: bool = ...
    ) -> Target | None: ...

    def get_target(
        self, name: str, raise_if_missing: bool = True, recursive: bool = True
    ) -> Target | None:
        """Get a target by (possibly qualified) name.

        Returns None if not found and raise_if_missing is False;
        otherwise raises KeyError.
        """

        project, target_name = split_qualified_name(name)
        if project is not None:
            if project == self.name:
                for target in self._targets:
                    if target.name == target_name:
                        return target
                else:
                    if raise_if_missing:
                        raise KeyError(f"Target '{name}' not found")
                    return None
        else:
            for target in self._targets:
                if target.name == target_name:
                    return target

        if recursive:
            targets_found = []
            for child in self._children:
                if (
                    target := child.get_target(name, raise_if_missing=False)
                ) is not None:
                    targets_found.append(target)
            if len(targets_found) > 1:
                raise KeyError(
                    f"Multiple targets named '{name}' found in child projects: "
                    f"{', '.join(t.qualified_name for t in targets_found)}\n"
                    f"Use qualified names (e.g., '{targets_found[0].qualified_name}') to disambiguate."
                )
            if targets_found:
                return targets_found[0]

        if raise_if_missing:
            raise KeyError(f"Target '{name}' not found")
        return None

    def get_targets(self, *names: str) -> list[Target]:
        """Get multiple targets by name; raises KeyError if any is missing."""
        return [self.get_target(name) for name in names]

    @property
    def targets(self) -> list[Target]:
        """Get all registered targets."""
        results: list[Target] = list(self._targets)
        for child in self._children:
            results.extend(child.targets)
        return results

    @property
    def environments(self) -> list[Env]:
        """Get all registered environments."""
        return list(self._environments)

    @property
    def default_environment(self) -> Env:
        """Get the default environment (first one registered).

        A sub-project that registers no environment of its own inherits the
        enclosing project's, so a library nested several levels down still
        finds the toolchain the top-level build set up.

        Raises:
            ValueError: If no environment is registered here or in any
                enclosing project.
        """
        project: Project | None = self
        while project is not None:
            if project._environments:
                return project._environments[0]
            project = project._parent
        raise ValueError(
            f"No environments have been registered in project '{self.name}' "
            f"or any enclosing project."
        )

    def Alias(
        self, name: str, *targets: Target | Node | list[Target | Node]
    ) -> AliasNode:
        """Create a named alias for targets, usable as a build target
        (e.g. 'ninja test'). Accepts Targets, Nodes, or lists of them."""
        if name not in self._aliases:
            self._aliases[name] = AliasNode(name, defined_at=get_caller_location())

        alias = self._aliases[name]
        # Flatten lists so Alias("name", [a, b]) works like Alias("name", a, b)
        flat: list[Target | Node] = []
        for t in targets:
            if isinstance(t, list):
                flat.extend(t)
            else:
                flat.append(t)
        for t in flat:
            match t:
                case Target():
                    # Defer resolution: output_nodes may not be populated until resolve()
                    alias.add_deferred_target(t)
                case Node():
                    alias.add_target(t)
                case _:
                    raise TypeError(
                        f"Alias targets must be Target, Node, or list of them, got {type(t)}"
                    )

        return alias

    def _iter_tree(self) -> Generator[Project, None, None]:
        """This project and every descendant, depth-first in creation order."""
        yield self
        for child in self._children:
            yield from child._iter_tree()

    @property
    def tree_aliases(self) -> dict[str, list[Node]]:
        """Every alias declared in this project's tree, name → its nodes.

        An alias is a user-level grouping, so one name declared at several
        levels of the tree is one group: the union of every declaration's
        targets, in tree order. This is what the generated build files
        expose as the alias; :attr:`aliases` stays this project's own
        declarations.
        """
        merged: dict[str, list[Node]] = {}
        seen: dict[str, set[int]] = {}
        for project in self._iter_tree():
            for name, alias in project._aliases.items():
                nodes = merged.setdefault(name, [])
                ids = seen.setdefault(name, set())
                for node in alias.targets:
                    if id(node) not in ids:
                        ids.add(id(node))
                        nodes.append(node)
        return merged

    def Default(self, *targets: Target | Node | str) -> None:
        """Set default targets for building.

        These are built when 'ninja' is run with no arguments.

        Args:
            *targets: Targets, output Nodes, or alias/target names to build
                by default. A Node must already be a registered output of
                some target (e.g. from ``env.Command()``); it is resolved to
                that owning target, since defaults are ultimately consumed
                as targets by the generators.

        Raises:
            ValueError: If a Node isn't the output of any registered target,
                or an alias resolves to no target-backed output.
            KeyError: If a string name matches neither an alias nor a target.
        """
        for t in targets:
            match t:
                case Target():
                    self._add_default_target(t)
                case Node():
                    target = self._find_target_for_node(t)
                    if target is None:
                        raise ValueError(
                            f"Default(): {t!r} is not an output of any "
                            f"target registered in project '{self.name}', "
                            "so it can't be used as a default build target. "
                            "Pass the owning Target instead, or call "
                            "Default() with the node after its target has "
                            "produced its outputs (e.g. after resolve())."
                        )
                    self._add_default_target(target)
                case str():
                    for target in self._resolve_default_name(t):
                        self._add_default_target(target)
                case _:
                    raise TypeError(
                        f"Default() arguments must be Target, Node, or str; "
                        f"got {type(t)!r}"
                    )

    def _add_default_target(self, target: Target) -> None:
        """Register `target` as a default build target, deduping by identity."""
        if target not in self._default_targets:
            self._default_targets.append(target)

    def _find_target_for_node(self, node: Node) -> Target | None:
        """Find the target (in this project or its children) that produced `node`.

        Searches intermediate/output nodes, so this only succeeds for nodes
        that already exist -- e.g. outputs of eagerly-built targets such as
        ``env.Command()``. Compile/link target outputs aren't populated
        until ``resolve()`` runs. Returns None if no target produced `node`.
        """
        for target in self.targets:
            if node in target.output_nodes or node in target.intermediate_nodes:
                return target
        return None

    def _resolve_default_name(self, name: str) -> list[Target]:
        """Resolve a Default() string argument to one or more targets.

        Tries `name` as an alias first (an alias may wrap several targets;
        every declaration in the tree counts — an alias is one group
        wherever its pieces were declared), then as a plain target name.
        """
        declarations = [
            p._aliases[name] for p in self._iter_tree() if name in p._aliases
        ]
        if declarations:
            resolved: list[Target] = []
            for alias in declarations:
                for target_ref in alias._target_refs:
                    if target_ref not in resolved:
                        resolved.append(target_ref)
                for node in alias._nodes:
                    target = self._find_target_for_node(node)
                    if target is not None and target not in resolved:
                        resolved.append(target)
            if not resolved:
                raise ValueError(
                    f"Default(): alias '{name}' does not resolve to any "
                    "target-backed build output, so it can't be used as a "
                    "default build target."
                )
            return resolved

        target = self.get_target(name, raise_if_missing=False)
        if target is not None:
            return [target]

        raise KeyError(
            f"Default(): '{name}' is not a known alias or target in "
            f"project '{self.name}'. Tried aliases "
            f"{sorted(self.tree_aliases)!r} and targets "
            f"{sorted(t.name for t in self.targets)!r}."
        )

    @property
    def default_targets(self) -> list[Target]:
        """Get the default build targets."""
        return list(self._default_targets)

    @property
    def aliases(self) -> dict[str, AliasNode]:
        """Get all defined aliases."""
        return dict(self._aliases)

    def all_nodes(self) -> set[Node]:
        """Collect all nodes from all targets."""
        return collect_all_nodes(self._targets)

    def _to_build_relative(self, p: Path) -> Path:
        """Strip the build_dir prefix from a canonicalized path.

        Used by get_child_nodes/has_child_nodes to normalize paths for
        comparison regardless of whether they include the build_dir prefix.
        """
        parts = p.parts
        bd_parts = self.build_dir.parts
        n = len(bd_parts)
        if bd_parts and parts[:n] == bd_parts:
            return Path(*parts[n:]) if len(parts) > n else Path(".")
        return p

    def _normalize_for_index(self, p: Path) -> Path:
        """The form paths are compared in by the child-node queries.

        One place, so the index and the queries can't drift apart.
        """
        return self._to_build_relative(self._path_resolver.canonicalize(p))

    def _child_keys(self, path: Path | str) -> list[Path]:
        """Registry keys strictly beneath *path* (see _ChildNodeIndex)."""
        self._child_index.sync(self._nodes, self._normalize_for_index, self.build_dir)
        return self._child_index.children(self._normalize_for_index(Path(path)))

    def get_child_nodes(self, path: Path | str) -> list[FileNode]:
        """Get all project nodes whose path is a descendant of the given path.

        Uses the same canonicalization as the node registry -- no filesystem
        access.  Both the query path and registered node paths are
        normalized to build-dir-relative form before comparison so that
        paths supplied with and without the ``build_dir`` prefix match.

        Args:
            path: Directory path to search under.

        Returns:
            List of FileNodes whose canonical path is strictly under *path*.
        """
        return [
            node
            for key in self._child_keys(path)
            if isinstance(node := self._nodes[key], FileNode)
        ]

    def has_child_nodes(self, path: Path | str) -> bool:
        """Check whether any registered node is a descendant of *path*.

        Like ``bool(self.get_child_nodes(path))``, except that it counts
        directory nodes too — it answers "is anything registered under here".
        """
        return bool(self._child_keys(path))

    # -------------------------------------------------------------------------
    # Configure-time inputs and staged generation
    # -------------------------------------------------------------------------

    def add_configure_dependency(self, path: Path | str | FileNode) -> None:
        """Declare a file the build description read while describing the build.

        Generated build files depend on these, so changing one re-runs pcons
        before anything is built (Ninja's ``generator = 1`` edge). The build
        script itself, any Python module imported from the project tree, and
        ``configure_file()`` templates are registered automatically; use this
        for data files a build script reads directly.

        The path may name a file the build itself produces — that is how
        staged generation works; see :meth:`generated_input`.
        """
        top = self.top
        if isinstance(path, FileNode):
            resolved = path.path
        else:
            resolved = self._canonicalize_path(Path(path))
        if resolved not in top._configure_deps:
            top._configure_deps.append(resolved)

    @property
    def configure_dependencies(self) -> list[Path]:
        """Files the generated build files depend on (see
        :meth:`add_configure_dependency`)."""
        return list(self.top._configure_deps)

    def generated_input(self, path: Path | str) -> Path | None:
        """A build-time-generated file the build description wants to read.

        Returns the path when it exists, otherwise None — and either way
        registers it as a configure dependency. That makes staged generation
        work: on the first pass the file does not exist yet, so the script
        describes only the part of the graph that produces it; the build
        system then produces it, re-runs pcons, and the second pass sees the
        complete picture.

        This is the one sanctioned existence check in a build script. Deciding
        whether something is a *target* by looking at the filesystem is still
        wrong; this asks whether a declared build input has been produced yet,
        and records the answer as a dependency.

        Example:
            manifest = project.generated_input(project.build_dir / "plugins.txt")
            if manifest is not None:
                for name in manifest.read_text().split():
                    project.SharedLibrary(name, env, sources=[f"{name}.c"])
        """
        top = self.top
        self.add_configure_dependency(path)

        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = top.root_dir / self._canonicalize_path(candidate)
        if candidate.is_file():
            return candidate

        pending = self._canonicalize_path(Path(path))
        if pending not in top._pending_stages:
            top._pending_stages.append(pending)
        return None

    def when_generated(
        self, *paths: Path | str
    ) -> Callable[[Callable[..., Any]], None]:
        """Decorator: run a block only once every named file has been generated.

        Sugar over :meth:`generated_input` for the common staged-generation
        shape. The decorated function is called immediately with the resolved
        paths when all of them exist, and skipped otherwise; either way the
        paths become configure dependencies, so the build system re-runs pcons
        once they appear.

        Example:
            @project.when_generated(build_dir / "plugins.txt")
            def _plugins(manifest: Path) -> None:
                for name in manifest.read_text().split():
                    make_plugin(name)
        """

        def decorate(func: Callable[..., Any]) -> None:
            resolved = [self.generated_input(p) for p in paths]
            if all(p is not None for p in resolved):
                func(*resolved)

        return decorate

    def _register_implicit_configure_deps(self) -> None:
        """Register the build script and every project-local module it imported.

        Splitting a build description across ``build-scripts/*.py`` is normal;
        those files are configure inputs just as much as the entry script, and
        ``sys.modules`` is the honest record of which ones were used.

        An in-tree virtualenv is excluded. ``uv venv`` puts one at ``.venv`` by
        default, and its site-packages are under the project root but are not
        the build description: every installed module would join the regen edge,
        so upgrading any dependency would re-run pcons.
        """
        import sys

        from pcons.core import invocation

        top = self.top
        root = top.root_dir.resolve()
        pcons_dir = Path(__file__).resolve().parent.parent

        inv = invocation.current()
        if inv is not None:
            top.add_configure_dependency(inv.script)

        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename or not filename.endswith(".py"):
                continue
            try:
                path = Path(filename).resolve()
            except OSError:
                continue
            if not path.is_relative_to(root) or path.is_relative_to(pcons_dir):
                continue
            if _in_virtualenv(path, root):
                continue
            top.add_configure_dependency(path)

    def _check_output_collisions(self) -> None:
        """One producer per output file, over the whole project tree.

        Node deduplication maps a path to one node, so two targets
        resolving to the same output would otherwise merge silently: the
        second's inputs pile onto the first's build edge, and an archive
        ends up holding both environments' objects with no warning
        (issue #96). Raise at the collision instead, naming both targets.
        """
        producers: dict[int, Target] = {}
        for target in self.targets:
            for node in target.output_nodes:
                other = producers.get(id(node))
                if other is None:
                    producers[id(node)] = target
                    continue
                if other is target:
                    continue
                env_names = {
                    env.name
                    for env in (other._env, target._env)
                    if env is not None and env.name
                }
                envs = ""
                if len(env_names) == 2:
                    envs = (
                        " They build in different environments, so an "
                        "environment-keyed output_prefix (e.g. "
                        'f"{env.name}/") would keep them apart.'
                    )
                raise PconsError(
                    f"targets {other.qualified_name!r} and "
                    f"{target.qualified_name!r} both build "
                    f"{node.path}.\n"
                    "Each output file must have one producer: give one target a "
                    f"distinct output_name or output_prefix, or split into multiple projects.{envs}"
                )

    def _check_pending_stages(self) -> None:
        """Verify every skipped staged input is something the build produces.

        A staged input that no edge produces can never appear, so the build
        would silently stay incomplete forever. Raise instead.
        """
        top = self.top
        if not top._pending_stages:
            return

        orphans = [p for p in top._pending_stages if not top._is_produced(p)]
        if orphans:
            from pcons.core.errors import PconsError

            listed = "\n  ".join(str(p) for p in orphans)
            raise PconsError(
                f"Staged input is not produced by any build rule:\n  {listed}\n"
                f"generated_input()/when_generated() wait for a file the build "
                f"itself generates. Declare the rule that produces it (e.g. "
                f"env.Command(target=..., ...)) in the same pass."
            )

        logger.info(
            "Staged generation: %d block(s) pending on %s — run the build to "
            "generate them; pcons re-runs automatically.",
            len(top._pending_stages),
            ", ".join(str(p) for p in top._pending_stages),
        )

    def _is_produced(self, path: Path) -> bool:
        """True if some node in the project tree builds *path*."""
        for project in self._tree():
            node = project._nodes.get(path)
            if node is not None and (
                getattr(node, "_build_info", None) is not None
                or getattr(node, "builder", None) is not None
            ):
                return True
        return False

    def _tree(self) -> list[Project]:
        """This project and all its descendants."""
        result: list[Project] = [self]
        for child in self._children:
            result.extend(child._tree())
        return result

    def validate(self) -> list[Exception]:
        """Validate the project configuration.

        Checks for:
        - Dependency cycles
        - Missing source files
        - Undefined targets referenced as dependencies

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[Exception] = []

        # Check for dependency cycles
        cycles = detect_cycles_in_targets(self._targets)
        for cycle in cycles:
            from pcons.core.errors import DependencyCycleError

            errors.append(DependencyCycleError(cycle))

        # Check for missing sources
        from pcons.core.errors import MissingSourceError

        for target in self._targets:
            for source in target.sources:
                if isinstance(source, FileNode):
                    # Only check source files (not generated files)

                    if source.builder is None:
                        p = source.path
                        if not p.is_absolute():
                            p = self.top.root_dir / p
                        if not p.exists():
                            errors.append(
                                MissingSourceError(str(p), target_name=target.name)
                            )

        return errors

    def build_order(self) -> list[Target]:
        """Get targets in the order they should be built.

        Returns:
            Targets sorted so dependencies come before dependents.
        """
        return topological_sort_targets(self._targets)

    def print_targets(self) -> None:
        """Print a human-readable summary of all targets.

        Useful for debugging. Shows target names, types, and dependencies.
        """
        print(f"Project: {self.name}")
        print(f"Build dir: {self.build_dir}")
        print(f"Targets ({len(self._targets)}):")

        for target in sorted(self._targets, key=lambda t: t.name):
            print(f"  {target.name} ({target.target_type})")
            if target.sources:
                print(f"    sources: {len(target.sources)} files")
            if target.output_nodes:
                for node in target.output_nodes[:3]:
                    print(f"    output: {node.path}")
                if len(target.output_nodes) > 3:
                    print(f"    ... and {len(target.output_nodes) - 3} more")
            if target.dependencies:
                deps = [
                    d.name if hasattr(d, "name") else str(d)
                    for d in target.dependencies
                ]
                print(f"    links: {', '.join(deps)}")

    def resolve(self, strict: bool = False) -> None:
        """Resolve all targets, populating their nodes for generation
        (see pcons.core.resolver for the phases), then validate.

        Args:
            strict: If True, raise on validation errors; otherwise
                log warnings and continue.
        """
        from pcons.core.resolver import Resolver

        resolver = Resolver(self)
        resolver.resolve()
        resolver.resolve_pending_sources()

        self._register_implicit_configure_deps()
        self._check_pending_stages()
        self._check_output_collisions()

        errors = self.validate()
        if errors:
            for error in errors:
                logger.warning("Validation: %s", error)
            if strict:
                from pcons.core.errors import PconsError

                raise PconsError(
                    f"Validation failed with {len(errors)} error(s). "
                    f"First error: {errors[0]}"
                )

        self._resolved = True

    def _output_graphs_if_requested(self) -> None:
        """Output dependency graphs if requested via PCONS_GRAPH/PCONS_MERMAID env vars.

        Called once the deferred-generation pass has drained, so the graph
        describes the same project the build files do. A script may call
        resolve() itself and go on adding targets, so writing from resolve()
        would snapshot a project still under construction.
        """
        if not (os.environ.get("PCONS_GRAPH") or os.environ.get("PCONS_MERMAID")):
            return
        if not self._resolved:
            self.resolve()

        def per_project(path_str: str) -> str:
            """One requested file can only serve one project: the first
            keeps the requested name, each later sibling gets the name
            suffixed with its own ("deps.dot" -> "deps-host.dot").
            Stdout needs no such split; the graphs just follow each other.
            """
            if path_str == "-":
                return path_str
            top_levels = Project._top_level_projects()
            if not top_levels or top_levels[0] is self.top:
                return path_str
            path = Path(path_str)
            return str(path.with_name(f"{path.stem}-{self.top.name}{path.suffix}"))

        graph_path = os.environ.get("PCONS_GRAPH")
        if graph_path:
            from pcons.generators.dot import DotGenerator

            self._output_graph(DotGenerator, per_project(graph_path), "DOT")

        mermaid_path = os.environ.get("PCONS_MERMAID")
        if mermaid_path:
            from pcons.generators.mermaid import MermaidGenerator

            self._output_graph(MermaidGenerator, per_project(mermaid_path), "Mermaid")

    def _output_graph(
        self,
        generator_class: type,
        output_path_str: str,
        format_name: str,
    ) -> None:
        """Write a dependency graph to stdout or a file.

        Writes it here and now. Queueing it with generator.generate() would
        put it on a pending list the caller is already draining, where nothing
        would run it.

        Args:
            generator_class: The generator class to instantiate.
            output_path_str: "-" for stdout, or a file path.
            format_name: Human-readable format name for log messages.

        Raises:
            PconsError: The destination cannot be written. The path came from
                the command line, so the message names it rather than letting
                an errno from mkdir or open reach the user as a traceback.
        """
        import sys

        gen = generator_class()
        if output_path_str == "-":
            gen.write(self, sys.stdout)
            return

        output_path = Path(output_path_str)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                gen.write(self, f)
        except OSError as e:
            from pcons.core.errors import PconsError

            raise PconsError(
                f"Cannot write {format_name} graph to {output_path_str}: {e.strerror}"
            ) from e
        logger.info("Wrote %s graph to %s", format_name, output_path_str)

    def _mark_generated(self):
        """Called by build-file generators; makes later generate() calls no-ops."""
        self.__generated = True

    def generate(self) -> None:
        """Ask for build files (convenience method).

        Selects the appropriate generator (Ninja by default, overridable
        via ``--generator`` CLI flag or ``PCONS_GENERATOR`` env var) and
        enqueues the generation rather than performing it. ``pcons`` runs
        what is enqueued once the build script has finished, resolving the
        project then, so a target created after this call is still built.

        Creating a top-level project enqueues this already, so a script
        need not call it at all.

        For advanced usage (e.g., disabling compile_commands.json),
        use ``Generator().generate(project)`` directly.
        """
        if not self.__generated:
            from pcons import Generator

            Generator().generate(self)

    def cli_command(
        self, name: str | None = None, **attrs: Any
    ) -> Callable[[Callable[..., Any]], UserCommand]:
        """Declare a CLI command, reachable as ``pcons run <name>``.

        Sugar for `pcons.cli_command`, which is the same registry. The entry
        records no project: the callback reaches this one by closing over it.
        """
        from pcons import commands

        return commands.cli_command(name, **attrs)

    def cli_group(
        self, name: str | None = None, **attrs: Any
    ) -> Callable[[Callable[..., Any]], UserGroup]:
        """Declare a CLI group, reachable as ``pcons run <name> <verb>``.

        Sugar for `pcons.cli_group`.
        """
        from pcons import commands

        return commands.cli_group(name, **attrs)

    def generate_pc_file(
        self,
        target: Target,
        *,
        version: str = "0.0.0",
        description: str = "",
        install_prefix: str = "/usr/local",
    ) -> Path:
        """Generate a pkg-config .pc file for a library target.

        Writes a standard .pc file based on the target's public usage
        requirements (include_dirs, link_libs, defines, link_flags).
        The file is written to the build directory and can be installed
        with ``project.Install("lib/pkgconfig", [pc_path])``.

        This runs at configure time (like ``configure_file()``), not as
        a ninja build step. Uses write-if-changed to avoid unnecessary
        downstream rebuilds.

        Args:
            target: The library target to generate a .pc file for.
            version: Package version string (e.g., "1.2.0").
            description: One-line package description.
            install_prefix: Expected install prefix. The .pc file uses
                ``${prefix}`` variables so it's relocatable.

        Returns:
            Path to the generated .pc file.

        Example:
            lib = project.StaticLibrary("foo", env, sources=["src/foo.c"])
            lib.public.include_dirs.append("include")

            pc = project.generate_pc_file(lib, version="1.2.0")
            project.Install("lib/pkgconfig", [pc])
        """
        from pcons.packages.imported import ImportedTarget

        name = target.name

        # Split the transitive dependency closure. A dependency that came from
        # pkg-config becomes a Requires: entry, and pkg-config resolves its
        # flags for us. Anything else — a header-only package like glm, or a
        # library found by prefix — has no .pc file to refer to, so its public
        # usage requirements must be written into this one or consumers will
        # fail to compile against us.
        requires: list[str] = []
        inlined: list[Target] = []
        seen: set[int] = {id(target)}
        queue = list(target.dependencies)
        while queue:
            dep = queue.pop(0)
            if id(dep) in seen:
                continue
            seen.add(id(dep))
            package = getattr(dep, "package", None)
            if isinstance(dep, ImportedTarget) and package is not None:
                if getattr(package, "found_by", None) == "pkg-config":
                    if dep.name not in requires:
                        requires.append(dep.name)
                    # pkg-config expands this dependency's own Requires.
                    continue
            inlined.append(dep)
            queue.extend(dep.dependencies)

        # Build Cflags: from public include_dirs and defines
        # Rewrite include dirs to use ${includedir} for relocatability:
        # - dirs under root_dir → ${includedir} (installed layout)
        # - relative dirs like "include" → ${includedir}
        # - absolute dirs outside the project → kept as-is
        contributors = [target, *inlined]

        cflags_parts: list[str] = []
        seen_includedir = False
        for source in contributors:
            for inc_dir in source.public.include_dirs:
                inc_path = Path(inc_dir)
                # `anchor` is the cross-platform test for "rooted". On Windows
                # a Unix-style path like /opt/x has an anchor but is not
                # `is_absolute()` (no drive), and str() has already turned its
                # separators into backslashes, so neither of those alone works.
                if not inc_path.anchor:
                    # Relative path (e.g., "include") — use ${includedir}
                    if not seen_includedir:
                        cflags_parts.append("-I${includedir}")
                        seen_includedir = True
                else:
                    # Absolute path — check if it's under the project root
                    try:
                        inc_path.relative_to(self.root_dir)
                        # Under project root — will be installed to ${includedir}
                        if not seen_includedir:
                            cflags_parts.append("-I${includedir}")
                            seen_includedir = True
                    except ValueError:
                        # External path (e.g., /usr/include) — keep as-is
                        if f"-I{inc_dir}" not in cflags_parts:
                            cflags_parts.append(f"-I{inc_dir}")
        for source in contributors:
            for define in source.public.defines:
                if f"-D{define}" not in cflags_parts:
                    cflags_parts.append(f"-D{define}")
            for flag in source.public.compile_flags:
                if str(flag) not in cflags_parts:
                    cflags_parts.append(str(flag))

        # Build Libs: the library itself plus any public link flags/libs.
        # Libraries covered by Requires: are left out — pkg-config adds them.
        libs_parts: list[str] = ["-L${libdir}", f"-l{name}"]
        for dep in inlined:
            # A sibling library in this project has no .pc to refer to here,
            # so name it directly; it installs alongside us in ${libdir}.
            if not getattr(dep, "is_imported", False) and dep.target_type in (
                "shared_library",
                "static_library",
            ):
                if f"-l{dep.name}" not in libs_parts:
                    libs_parts.append(f"-l{dep.name}")
        for source in contributors:
            for lib_dir in source.public.link_dirs:
                if f"-L{lib_dir}" not in libs_parts:
                    libs_parts.append(f"-L{lib_dir}")
            for flag in source.public.link_flags:
                libs_parts.append(str(flag))
            for lib in source.public.link_libs:
                # Target entries are dependencies in their own right and are
                # already accounted for by the closure walk above.
                if isinstance(lib, Target):
                    continue
                if lib not in requires and f"-l{lib}" not in libs_parts:
                    libs_parts.append(f"-l{lib}")

        # Write .pc content
        lines = [
            f"prefix={install_prefix}",
            "libdir=${prefix}/lib",
            "includedir=${prefix}/include",
            "",
            f"Name: {name}",
            f"Description: {description or name}",
            f"Version: {version}",
        ]
        if requires:
            lines.append(f"Requires: {' '.join(requires)}")
        lines.append(f"Libs: {' '.join(libs_parts)}")
        if cflags_parts:
            lines.append(f"Cflags: {' '.join(cflags_parts)}")

        content = "\n".join(lines) + "\n"

        # Write-if-changed. build_dir may be relative to root_dir (the usual
        # case), so resolve it to an absolute location independent of the
        # current working directory.
        build_dir = (
            self.build_dir
            if self.build_dir.is_absolute()
            else self.root_dir / self.build_dir
        )
        pc_path = build_dir / f"{name}.pc"
        pc_path.parent.mkdir(parents=True, exist_ok=True)
        if pc_path.exists() and pc_path.read_text() == content:
            return pc_path
        pc_path.write_text(content, encoding="utf-8")
        logger.info("Generated %s", pc_path)
        return pc_path

    # =========================================================================
    # Package Discovery
    # =========================================================================

    @overload
    def find_package(
        self,
        name: str,
        *,
        version: str | None = None,
        components: Sequence[str] | None = None,
        required: Literal[True] = True,
        system: bool = False,
    ) -> Target: ...

    @overload
    def find_package(
        self,
        name: str,
        *,
        version: str | None = None,
        components: Sequence[str] | None = None,
        required: bool,
        system: bool = False,
    ) -> Target | None: ...

    def find_package(
        self,
        name: str,
        *,
        version: str | None = None,
        components: Sequence[str] | None = None,
        required: bool = True,
        system: bool = False,
    ) -> Target | None:
        """Find an external package and return it as an ImportedTarget.

        Searches for the package using the configured finder chain
        (default: PkgConfigFinder → SystemFinder). Results are cached
        so repeated calls with the same arguments return the same target.

        The returned target can be used as a dependency via target.link()
        or applied directly to an environment via env.use().

        Args:
            name: Package name (e.g., "zlib", "openssl").
            version: Optional version requirement (e.g., ">=3.0").
            components: Optional list of package components.
            required: If True (default), raises PackageNotFoundError when
                     the package is not found. If False, returns None.
            system: If True, the package's include directories are treated as
                   system headers (-isystem, /external:I), so warnings from
                   its headers are suppressed in every dependent. Off by
                   default: -isystem on a directory the compiler already
                   searches (/usr/include) reorders the search and can break
                   the standard library. Use it for prefixes owned by a
                   package manager or a fetched source tree.

        Returns:
            An ImportedTarget representing the package, or None if not
            found and required=False.

        Raises:
            PackageNotFoundError: If the package is not found and required=True.

        Example:
            zlib = project.find_package("zlib")
            openssl = project.find_package("openssl", version=">=3.0")
            boost = project.find_package("boost", components=["filesystem"])
            doctest = project.find_package("doctest", system=True)

            app.link(zlib)
            env.use(openssl)
        """
        cache_key = (name, version, tuple(components or []), system)
        # One package is one target, and a target name is unique, so the two
        # spellings of the same package cannot both exist. Say which two,
        # rather than letting Target.__init__ report a name collision. A
        # cached None is a package that was never found, so it owns no target
        # and conflicts with nothing.
        conflicting = next(
            (
                k
                for k, found in self._found_packages.items()
                if k[:3] == cache_key[:3] and k[3] != system and found is not None
            ),
            None,
        )
        if conflicting is not None:
            raise ValueError(
                f"Package '{name}' was already found with system={conflicting[3]}; "
                f"requesting system={system} would need a second target of the "
                f"same name. Pick one spelling for the whole project."
            )
        if cache_key not in self._found_packages:
            if self._package_finder_chain is None:
                from pcons.packages.finders import (
                    FinderChain,
                    PkgConfigFinder,
                    SystemFinder,
                )

                self._package_finder_chain = FinderChain(
                    [PkgConfigFinder(), SystemFinder()]
                )

            pkg = self._package_finder_chain.find(name, version, components)
            if pkg is None:
                # Cache the negative result too: don't re-run the finder
                # chain (and its subprocesses) for every repeat probe.
                self._found_packages[cache_key] = None
            else:
                from pcons.packages.imported import ImportedTarget

                self._found_packages[cache_key] = ImportedTarget.from_package(
                    pkg, components=components, system=system
                )

        target = self._found_packages[cache_key]
        if target is None and required:
            from pcons.core.errors import PackageNotFoundError

            raise PackageNotFoundError(name, version)
        return target

    def add_package_finder(self, finder: Any) -> None:
        """Add a package finder to the front of the search chain.

        Custom finders are tried before the default finders (PkgConfig,
        System). Use this to add Conan, vcpkg, or custom finders.

        Args:
            finder: A BaseFinder instance.

        Example:
            from pcons.packages.finders import ConanFinder

            project.add_package_finder(ConanFinder(config, conanfile="conanfile.txt"))
            zlib = project.find_package("zlib")  # Tries Conan first
        """
        if self._package_finder_chain is None:
            from pcons.packages.finders import (
                FinderChain,
                PkgConfigFinder,
                SystemFinder,
            )

            self._package_finder_chain = FinderChain(
                [finder, PkgConfigFinder(), SystemFinder()]
            )
        else:
            self._package_finder_chain.add(finder)

    # Command is kept as a wrapper since it delegates to env.Command()
    # and doesn't fit the registry pattern well

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        lines = [f"Project: {self.name}"]
        lines.append(f"  Root: {self.root_dir}")
        lines.append(f"  Build: {self.build_dir}")
        lines.append(f"  Targets: {len(self._targets)}")
        for target in self._targets[:5]:
            target_type = target.target_type or "unknown"
            lines.append(f"    - {target.name} ({target_type})")
        if len(self._targets) > 5:
            lines.append(f"    ... and {len(self._targets) - 5} more")
        lines.append(f"  Environments: {len(self._environments)}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"Project({self.name!r}, "
            f"targets={len(self._targets)}, "
            f"envs={len(self._environments)})"
        )

    if not TYPE_CHECKING:
        # __getattr__ is hidden from type checkers so that unknown attribute
        # access on a Project is rejected (and typed builder methods from
        # _ProjectBuilders take effect). At runtime it dispatches registered
        # builders via the BuilderRegistry. User-registered @builder targets
        # are not in _ProjectBuilders, so calls to them appear as unresolved
        # attributes to type checkers and require a `type: ignore` /
        # `ty: ignore` at the call site (see examples/15_custom_builder).
        def __getattr__(self, name: str) -> Any:
            """Dispatch registered builders as Project methods,
            e.g. `project.InstallSymlink(...)` for @builder("InstallSymlink")."""
            registration = BuilderRegistry.get(name)
            if registration is not None:
                if registration.platforms:
                    import sys

                    if sys.platform not in registration.platforms:
                        raise AttributeError(
                            f"Builder '{name}' is only available on "
                            f"{', '.join(registration.platforms)} "
                            f"(current platform: {sys.platform})"
                        )
                return self._make_builder_method(registration)

            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )

    def __dir__(self) -> list[str]:
        """Include registered builder names so IDEs can complete them."""
        attrs = list(super().__dir__())
        attrs.extend(BuilderRegistry.names())
        return attrs

    def _make_builder_method(self, registration: Any) -> Any:
        """Create a bound method for a registered builder, injecting the
        project and capturing the caller location."""
        create_target = registration.create_target

        import inspect

        sig = inspect.signature(create_target)
        accepts_defined_at = "defined_at" in sig.parameters

        def builder_method(*args: Any, **kwargs: Any) -> Target:
            if accepts_defined_at and "defined_at" not in kwargs:
                kwargs["defined_at"] = get_caller_location()
            return create_target(self, *args, **kwargs)

        if hasattr(create_target, "__doc__"):
            builder_method.__doc__ = create_target.__doc__

        return builder_method
