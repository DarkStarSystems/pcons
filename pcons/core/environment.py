# SPDX-License-Identifier: MIT
"""Environment with namespaced tool configuration.

An Environment holds configuration for a build, including tool-specific
namespaces (env.cc, env.cxx, etc.) and cross-tool variables.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections import UserList
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from difflib import get_close_matches
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, TypeAlias, cast

from pcons.core.debug import trace, trace_value
from pcons.core.names import validate_name
from pcons.core.subst import Namespace, subst, to_shell_command
from pcons.core.toolconfig import ToolConfig
from pcons.core.vars import _record_variant
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.configure.platform import Platform
    from pcons.core._environment_stubs import _EnvironmentStubs
    from pcons.core._preset_names import KnownFeaturePreset
    from pcons.core._toolchain_names import KnownToolchain
    from pcons.core.explain import Explanation
    from pcons.core.node import FileNode
    from pcons.core.preset import Intent, Preset, ToolContribution
    from pcons.core.target import Target
    from pcons.tools import pybuilder
    from pcons.tools.toolchain import Toolchain
else:
    # At runtime, Environment inherits from `object`; tool namespaces and
    # cross-tool variables are dispatched through __getattr__/__setattr__
    # as before. The mixin's only purpose is to declare typed names for
    # static analysis.
    _EnvironmentStubs = object

logger = logging.getLogger(__name__)


#: ``$SOURCE`` or ``${SOURCE}``, but not ``$SOURCES`` or ``${SOURCES[0]}``.
PLACEMENT_VARS = frozenset(
    {"build_prefix", "runtime_directory", "library_directory", "archive_directory"}
)

_OUTPUT_DIRECTORY_VARS: dict[str, str] = {
    "program": "runtime_directory",
    "shared_library": "library_directory",
    "static_library": "archive_directory",
}

_SINGULAR_SOURCE = re.compile(r"\$SOURCE(?![S\w])|\$\{SOURCE\}")


#: One token of a list-form command, as written.
CommandToken: TypeAlias = "str | Path | Target | FileNode"

#: What ``env.Command(target=...)`` takes, one file or several.
CommandTargets: TypeAlias = "str | Path | Sequence[str | Path]"

#: What ``env.Command(source=...)`` takes, one input or several.
CommandSources: TypeAlias = "Target | str | Path | Sequence[Target | str | Path]"


def _as_list(value: Any) -> list[Any]:
    """One file or several, as a list; nothing at all is an empty list."""
    from pcons.core.node import Node
    from pcons.core.target import Target

    if value is None:
        return []
    if isinstance(value, (str, Path, Node, Target)):
        return [value]
    return list(value)


def _is_depfile_suffix(depfile: str | Path) -> bool:
    """True if *depfile* names the file after the output (".d"), rather
    than being the file's own path: a string of one leading dot and no
    directory."""
    return (
        isinstance(depfile, str)
        and re.fullmatch(r"\.[^./\\][^/\\]*", depfile) is not None
    )


def _warn_if_source_reads_as_singular(
    command: str | Sequence[Any], sources: int, at: SourceLocation
) -> None:
    """Flag ``$SOURCE`` written where more than one source will land.

    The two spellings mean the same thing -- every source -- but only the
    plural one says so. Written in the singular against several sources, the
    author almost certainly expects one, and the extras arrive as arguments
    the command never asked for. A script that reads ``sys.argv`` by
    membership rather than by position will appear to work for months.

    A warning rather than an error: consuming every source is a perfectly
    good thing for a command to do. Saying it as ``$SOURCES`` is silent.
    """
    if sources < 2:
        return
    text = command if isinstance(command, str) else " ".join(map(str, command))
    if not _SINGULAR_SOURCE.search(text):
        return
    logger.warning(
        "%s: $SOURCE with %d sources expands to all of them, so the rest "
        "reach the command as arguments. Write $SOURCES to say that is what "
        "you meant, or ${SOURCES[0]} to name only the first -- either way "
        "every source stays a dependency.",
        at,
        sources,
    )


def _names(toolchains: Sequence[Any]) -> str:
    """Toolchain names for a message: "gfortran", "flang and ifx"."""
    names = [t.name for t in toolchains]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _unrealized_arch(arch: str, missing: Sequence[Any]) -> ValueError:
    return ValueError(
        f"{_names(missing)} can't build for target arch '{arch}', so its "
        f"sources would be built for the host. Build them in an environment "
        f"of their own, or use a cross preset (see docs/presets.md)."
    )


def _unrealized_runtime(linkage: str, missing: Sequence[Any]) -> ValueError:
    return ValueError(
        f"{_names(missing)} can't link its runtime {linkage}, and "
        f"set_runtime_linkage({linkage!r}) applies to every compiler in the "
        f"environment."
    )


def _unrealized_feature(name: str, missing: Sequence[Any]) -> ValueError:
    return ValueError(
        f"Preset '{name}' isn't available with {_names(missing)}, so it can't "
        f"apply to the whole environment. Compile the sources it can't take in "
        f"a clone of the environment made before the preset "
        f"(target.add_sources(sources, env=clone)), or check first with "
        f"env.has_preset({name!r})."
    )


def _first_repr(value: Any) -> str:
    """repr of a sequence's first element, for use in an example line."""
    items = list(value)
    return repr(items[0]) if items else '"..."'


class Environment(_EnvironmentStubs):
    """Build environment with namespaced tool configuration.

    Provides namespaced access to tool configuration:
        env.cc.cmd = 'gcc'
        env.cc.flags = ['-Wall', '-O2']
        env.cxx.flags = ['-std=c++20']

    Cross-tool variables are accessed directly:
        env.build_dir = 'build/release'
        env.variant = 'release'

    An environment owns where its targets are built:
        env.build_prefix = 'mcu'          # everything it writes, below build/
        env.archive_directory = 'lib'     # static libraries, below that
        env.library_directory = 'lib'     # shared libraries and Windows import libs
        env.runtime_directory = 'bin'     # programs

    Environments can be cloned for variant builds:
        debug = env.clone()
        debug.cc.flags += ['-g']

    Attributes:
        build_dir: Directory for build outputs, with build_prefix applied.
        build_prefix: Directory below the build directory holding everything
            this environment writes, outputs and intermediates alike. It sits
            under an explicitly set build_dir and above the add_subdirectory
            offset, so 'build/rel' plus 'mcu' is build/rel/mcu while a
            sub-project of a plain build is build/mcu/sub.
        runtime_directory: Directory for program outputs, below build_dir.
        library_directory: Directory for shared library outputs, below build_dir.
        archive_directory: Directory for static library outputs, and for Windows
            import libraries, below build_dir.
        defined_at: Source location where this environment was created.
    """

    __slots__ = (
        "_tools",
        "_vars",
        "_build_dir_base",
        "_build_dir_offset",
        "_project",
        "_toolchain",
        "_additional_toolchains",
        "_created_nodes",
        "_applied_presets",
        "_applied_imperative",
        "_use_origins",
        "_intents",
        "_replaying",
        "_tool_owners",
        "_name",
        "_cross_preset",
        "_target",
        "defined_at",
    )

    # Standalone tool namespaces installed by `_setup_standalone_tools()`
    # regardless of which toolchain is active. The generator reads this
    # when building the Environment typing stub.
    STANDALONE_TOOL_NAMES: ClassVar[tuple[str, ...]] = ("install", "archive")

    def __init__(
        self,
        *,
        name: str | None = None,
        toolchain: Toolchain | KnownToolchain | str | Sequence[str] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> None:
        """Create an environment.

        Args:
            name: Optional name for this environment (used in ninja rule names).
            toolchain: Optional toolchain to initialize tools from. A string
                is looked up in the toolchain registry: a finder name like
                "c" auto-detects, a specific alias like "gcc" requires that
                toolchain. A sequence of names is a preference list.
            defined_at: Source location where this was created.
        """
        if isinstance(toolchain, str | Sequence):
            from pcons.tools.toolchain import toolchain_registry

            toolchain = toolchain_registry.resolve(
                cast("str | Sequence[str]", toolchain)
            )
        self._tools: dict[str, ToolConfig] = {}
        self._vars: dict[str, Any] = {
            "build_dir": Path("build"),
            "variant": "default",
            "build_prefix": None,
            "runtime_directory": None,
            "library_directory": None,
            "archive_directory": None,
        }
        self._build_dir_base = Path("build")
        self._build_dir_offset = Path()
        from pcons.core.project import Project

        self._project = Project.current()
        if self._project is not None:
            self._set_project_build_dir(
                self._project.top._build_dir, self._project._build_dir
            )

        self._toolchain = toolchain
        self._additional_toolchains: list[Toolchain] = []
        self._created_nodes: list[Any] = []  # Nodes created by builders
        self._applied_presets: list[Preset] = []  # Presets applied, in order
        # Imperative escape-hatch presets that ran: (name, description)
        self._applied_imperative: list[tuple[str, str]] = []
        # Values env.use() put on tool variables, attributed to their package:
        # (field, value) -> (package name, "package"). Read by
        # compute_effective_requirements so `pcons explain` names the package
        # rather than the tool variable the value landed on.
        self._use_origins: dict[tuple[str, str], tuple[str, str]] = {}
        # What the script asked for, in order, for toolchains added later.
        # A program links its language runtimes one way or another: the
        # shared ones until set_runtime_linkage() says otherwise.
        from pcons.core.preset import RUNTIME_LINKAGE_GROUP, Intent

        self._intents: list[Intent] = [
            Intent("runtime", "shared", group=RUNTIME_LINKAGE_GROUP)
        ]
        # Set while add_toolchain() configures the toolchain it adds
        self._replaying = False
        # Tool name -> the toolchain that brought it, the only one that
        # realizes presets for it (see apply_from)
        self._tool_owners: dict[str, Toolchain] = {}
        self._cross_preset: Any = None
        self._target: Platform | None = None
        if name is not None:
            validate_name("Environment", name)
        self._name = name
        self.defined_at = defined_at or get_caller_location()

        # Validate toolchain type early, before any access (strings and
        # sequences were already resolved via the registry above)
        if toolchain is not None and not hasattr(toolchain, "setup"):
            raise TypeError(
                f"toolchain must be a Toolchain object or a registered toolchain "
                f'name like "c" or "gcc", got {type(toolchain).__name__}'
            )

        trace("env", "Creating environment: %s", name or "(unnamed)")
        trace_value("env", "defined_at", self.defined_at)
        if toolchain:
            trace_value("env", "toolchain", toolchain.name)

        # Initialize tools from toolchain if provided
        if toolchain is not None:
            toolchain.setup(self)
            self._record_toolchain_baseline(toolchain, list(self._get_tools().keys()))
            self._tool_owners = dict.fromkeys(self._get_tools(), toolchain)
            # After the baseline, so explain() attributes these by name.
            for preset in toolchain.setup_presets(self):
                self.apply_from(toolchain, preset)
            self._realize(self._runtime_intent(), [toolchain])

        # Always add standalone tools (install, archive)
        # These are tool-agnostic and always available
        self._setup_standalone_tools()

    # Private helper methods to reduce object.__getattribute__ verbosity
    def _get_tools(self) -> dict[str, ToolConfig]:
        """Get the internal tools dictionary."""
        tools: dict[str, ToolConfig] = object.__getattribute__(self, "_tools")
        return tools

    def _get_vars(self) -> dict[str, Any]:
        """Get the internal variables dictionary."""
        vars_dict: dict[str, Any] = object.__getattribute__(self, "_vars")
        return vars_dict

    def _get_created_nodes(self) -> list[Any]:
        """Get the internal created nodes list."""
        nodes: list[Any] = object.__getattribute__(self, "_created_nodes")
        return nodes

    def _setup_standalone_tools(self) -> None:
        """Set up standalone tools that are always available.

        Standalone tools don't require toolchains or external program detection.
        They provide builders for common operations like file installation and
        archive creation.
        """
        from pcons.tools.archive import ArchiveTool
        from pcons.tools.install import InstallTool

        InstallTool().setup(self)
        ArchiveTool().setup(self)

    def __getattr__(self, name: str) -> Any:
        """Get a tool namespace or cross-tool variable.

        Tool namespaces take precedence over variables.
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        # Check for tool namespace first
        tools = self._get_tools()
        if name in tools:
            return tools[name]

        # Check for cross-tool variable
        vars_dict = self._get_vars()
        if name in vars_dict:
            return vars_dict[name]

        properties = sorted(
            attr
            for klass in type(self).__mro__
            for attr, member in vars(klass).items()
            if isinstance(member, property) and not attr.startswith("_")
        )
        close = get_close_matches(name, [*tools, *vars_dict, *properties], n=1)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        raise AttributeError(
            f"Environment has no tool or variable '{name}'.{hint} "
            f"Tools: {', '.join(tools.keys()) or '(none)'}. "
            f"Vars: {', '.join(vars_dict.keys()) or '(none)'}. "
            f"Properties: {', '.join(properties)}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a cross-tool variable or replace a tool config."""
        if name.startswith("_") or name == "defined_at":
            object.__setattr__(self, name, value)
        elif isinstance(value, ToolConfig):
            tools = self._get_tools()
            tools[name] = value
            value._env = self
        elif name == "name":
            if value is not None:
                validate_name("Environment", value)
                self._refuse_taken_name(value)
            object.__setattr__(self, "_name", value)
        elif name in PLACEMENT_VARS:
            self._set_placement(name, value)
        elif name == "build_dir":
            object.__setattr__(self, name, value)
        else:
            vars_dict = self._get_vars()
            vars_dict[name] = value

    def _set_placement(self, name: str, value: str | Path | None) -> None:
        """Store one of the placement directories, rejecting what cannot be one."""
        from pcons.core.errors import PconsError

        if value is None or value == "":
            self._get_vars()[name] = None
        else:
            path = Path(value)
            if path.is_absolute():
                raise PconsError(
                    f"Environment.{name} must be relative to the build directory, "
                    f"got the absolute path {str(path)!r}."
                )
            if ".." in path.parts:
                raise PconsError(
                    f"Environment.{name} must stay inside the build directory, "
                    f"got {str(path)!r}."
                )
            self._get_vars()[name] = path
        if name == "build_prefix":
            self._get_vars()["build_dir"] = self._effective_build_dir()

    def _set_project_build_dir(self, top_build_dir: Path, build_dir: Path) -> None:
        """Anchor this environment on its project's build directory.

        The top-level directory becomes the base and the ``add_subdirectory``
        remainder the offset, so ``build_prefix`` lands between the two and a
        sub-project keeps its shape inside the environment's slice instead of
        the offset being applied twice.
        """
        base = Path(top_build_dir)
        try:
            offset = Path(build_dir).relative_to(base)
        except ValueError:
            base, offset = Path(build_dir), Path()
        self._build_dir_base = base
        self._build_dir_offset = offset
        self._get_vars()["build_dir"] = self._effective_build_dir()

    def build_dir_for(self, subdir: Path) -> Path:
        """The directory a target at *subdir* builds in.

        The offset is the target's rather than this environment's: an
        environment defined in one project may build targets declared in
        another, and it is the target's place in the tree that decides.

        Args:
            subdir: The target's offset from the top-level root, empty for a
                target of the top-level project.

        Returns:
            The build directory, ``build_prefix`` included.
        """
        base: Path = self._build_dir_base
        prefix = self._get_vars().get("build_prefix")
        result = base / prefix if prefix else base
        return result / subdir if subdir.parts else result

    @property
    def build_dir(self) -> Path:
        """Where a relative ``target=`` of the running script lands, absolute.

        This environment's build directory with its ``build_prefix``, below
        the ``add_subdirectory`` directory of the script asking, so
        ``target=env.build_dir / "x"`` and ``target="x"`` name one file.
        Assigning it names the whole directory, from the top-level root.
        """
        from pcons.core.project import Project

        offset = (
            Project.current()._node_offset
            if Project.has_current()
            else self._build_dir_offset
        )
        path = self.build_dir_for(offset)
        return self._project.top.root_dir / path if self._project else path

    @build_dir.setter
    def build_dir(self, value: str | Path) -> None:
        self._build_dir_base = Path(value)
        self._build_dir_offset = Path()
        self._get_vars()["build_dir"] = self._effective_build_dir()

    def _effective_build_dir(self) -> Path:
        """This environment's own build directory.

        The prefix goes below the named directory and above the
        ``add_subdirectory`` offset. Assigning ``build_dir`` names the whole
        directory, which clears the offset, so an explicitly set build
        directory keeps the prefix under it.
        """
        return self.build_dir_for(self._build_dir_offset)

    def _refuse_taken_name(self, name: str) -> None:
        """Raise if another environment of this project already answers to *name*.

        The name is half of a target's identity, so two environments sharing one
        would leave ``common@mcu`` meaning two things. Scoped to one project: a
        sub-project may have its own ``host`` environment, since its targets are
        spelled ``child::app@host``. Called wherever an environment gets its
        name: ``Project.Environment()``, and assignment afterwards. An
        environment with no project yet has nothing to clash with.
        """
        from pcons.core.errors import PconsError

        project = self._project
        if project is None:
            return
        for other in project._environments:
            if other is not self and other.name == name:
                raise PconsError(
                    f"Project '{project.name}' already has an environment named "
                    f"'{name}' (defined at {other.defined_at}). The name says "
                    f"which environment a target was built in, so it has to be "
                    f"unique in a project. Leave one unnamed, or name them apart."
                )

    def output_directory_for(self, target_type: str | None) -> Path | None:
        """The directory this environment places *target_type* outputs in.

        Relative to the environment's build directory, or None to leave them at
        its root. Object files and other intermediates are never placed here;
        they follow ``build_prefix`` only.
        """
        attr = _OUTPUT_DIRECTORY_VARS.get(target_type or "")
        if attr is None:
            return None
        directory: Path | None = self._get_vars().get(attr)
        return directory

    def add_tool(self, name: str, config: ToolConfig | None = None) -> ToolConfig:
        """Add or get a tool namespace.

        If the tool already exists, returns it. Otherwise creates
        a new ToolConfig.

        Args:
            name: Tool name (e.g., 'cc', 'cxx').
            config: Optional existing config to use.

        Returns:
            The ToolConfig for this tool.
        """
        tools = self._get_tools()
        if name in tools:
            return tools[name]
        if config is None:
            config = ToolConfig(name)
        tools[name] = config
        config._env = self
        return config

    def has_tool(self, name: str) -> bool:
        """Check if a tool namespace exists."""
        return name in self._get_tools()

    @property
    def toolchain(self) -> Toolchain:
        """The primary toolchain this environment was created with.

        Raises if the environment has no toolchain — use ``env.toolchains``
        (an empty list in that case) to probe without raising.
        """
        toolchain: Toolchain | None = object.__getattribute__(self, "_toolchain")
        if toolchain is None:
            raise AttributeError("this Environment was created without a toolchain")
        return toolchain

    def toolchain_for(self, tool: str) -> Toolchain | None:
        """The toolchain that owns *tool*: the one that added it, whose
        realizations reach it. A tool no toolchain added belongs to the
        primary toolchain. None in an environment without a toolchain."""
        primary: Toolchain | None = object.__getattribute__(self, "_toolchain")
        return self._tool_owners.get(tool, primary)

    def add_toolchain(self, toolchain: Toolchain | KnownToolchain | str) -> None:
        """Add an additional toolchain to this environment.

        Additional toolchains provide extra source handlers and tools.
        The primary toolchain (from constructor) has precedence for
        output naming conventions.

        Args:
            toolchain: Toolchain to add. A string is looked up in the
                toolchain registry, like the Environment constructor.

        Whatever the script already asked for (``set_variant``,
        ``apply_preset``, ``apply_cross_preset``, ``env.cxx.set_standard``,
        ...) is realized for the tools it brings, in order, as if it had
        been there from the start.

        Its objects are linked with the primary toolchain's, so it must
        follow a compatible ABI (Toolchain.abi). A name picks among the
        compatible toolchains ("fortran" beside MSVC finds flang or ifx,
        not MinGW gfortran); an incompatible one is an error. Only this
        environment's primary toolchain counts: other environments in the
        project may follow other ABIs.

        Example:
            env = project.Environment(toolchain="c")
            env.add_toolchain("cuda")  # Adds CUDA support
        """
        from pcons.tools.toolchain import abi_compatible, toolchain_registry

        primary = object.__getattribute__(self, "_toolchain")
        abi = getattr(primary, "abi", None) if primary is not None else None
        if isinstance(toolchain, str):
            try:
                toolchain = toolchain_registry.resolve(toolchain, abi=abi)
            except RuntimeError as e:
                if abi is None:
                    raise
                raise RuntimeError(
                    f"Can't add '{toolchain}' beside {primary.name}, whose "
                    f"objects follow the '{abi}' ABI: {e}"
                ) from e
        elif not abi_compatible(getattr(toolchain, "abi", None), abi):
            raise ValueError(
                f"Can't add {toolchain.name} beside {primary.name}: it follows "
                f"the '{toolchain.abi}' ABI and {primary.name} the '{abi}' one, "
                f"so their objects can't be linked together."
            )
        self._check_replay(toolchain)
        additional: list[Toolchain] = object.__getattribute__(
            self, "_additional_toolchains"
        )
        additional.append(toolchain)
        before = set(self._get_tools().keys())
        toolchain.setup(self)
        new_tools = [n for n in self._get_tools() if n not in before]
        self._record_toolchain_baseline(toolchain, new_tools)
        self._tool_owners.update(dict.fromkeys(new_tools, toolchain))
        # It configures only the tools it brought: $FC from an added Fortran
        # toolchain must not retarget the primary toolchain's linker. Then it
        # realizes everything the script already asked for, for those tools,
        # as if it had been there from the start (an added ifx gets /MDd
        # under debug, as cl does), and the environment keeps its variant
        # name.
        current_variant = self.variant
        self._replaying = True
        try:
            for preset in toolchain.setup_presets(self):
                self.apply_from(toolchain, preset)
            for intent in self._intents:
                self._realize(intent, [toolchain], replaying=True)
        finally:
            self._replaying = False
        self.variant = current_variant

    @property
    def toolchains(self) -> list[Toolchain]:
        """Return all toolchains (primary + additional).

        The primary toolchain (passed to constructor) is first in the list,
        followed by additional toolchains in the order they were added.

        Returns:
            List of all toolchains, or empty list if none configured.
        """
        result: list[Toolchain] = []
        primary: Toolchain | None = object.__getattribute__(self, "_toolchain")
        if primary is not None:
            result.append(primary)
        additional: list[Toolchain] = object.__getattribute__(
            self, "_additional_toolchains"
        )
        result.extend(additional)
        return result

    def tool_names(self) -> list[str]:
        """Return list of configured tool names."""
        return list(self._get_tools().keys())

    def register_node(self, node: Any) -> None:
        """Register a node created by a builder.

        This tracks nodes so the generator can find all build targets.

        Args:
            node: The node to register.
        """
        self._get_created_nodes().append(node)

    @property
    def created_nodes(self) -> list[Any]:
        """Return list of nodes created by builders in this environment."""
        return self._get_created_nodes()

    @property
    def name(self) -> str | None:
        """The environment name, if set.

        Assignment goes through ``__setattr__``, which validates the new name.
        """
        return self._name

    @name.setter
    def name(self, value: str | None) -> None:
        """Never runs: ``__setattr__`` takes the assignment first. Here so the
        property stays read-write to type checkers, and delegating so it cannot
        drift from the assignment that does run.
        """
        setattr(self, "name", value)  # noqa: B010

    def get(self, name: str, default: Any = None) -> Any:
        """Get a variable or tool with a default."""
        try:
            return getattr(self, name)
        except AttributeError:
            return default

    def subst(
        self,
        template: str | list[str],
        *,
        shell: str = "auto",
        **extra: Any,
    ) -> str:
        """Expand variables in a template and return as shell command string.

        Uses both tool namespaces and cross-tool variables. The template
        is expanded to a list of tokens, then converted to a properly
        quoted shell command string.

        Args:
            template: String or list with $var or ${tool.var} references.
            shell: Target shell for quoting ("auto", "bash", "cmd", "powershell", "ninja").
                   Use "ninja" when generating ninja build files.
            **extra: Additional variables for this expansion only.

        Returns:
            Expanded shell command string.
        """
        namespace = self._build_namespace()
        if extra:
            namespace.update(extra)
        tokens = subst(template, namespace)
        return to_shell_command(tokens, shell=shell)

    def subst_list(self, template: str | list[str], **extra: Any) -> list[str]:
        """Expand variables and return as list of tokens.

        Args:
            template: String or list with variable references.
            **extra: Additional variables for this expansion only.

        Returns:
            List of expanded tokens.
        """
        from typing import cast

        namespace = self._build_namespace()
        if extra:
            namespace.update(extra)
        # subst() returns list[str] for string/list templates (not MultiCmd)
        # Cast is safe because template is str | list[str], not MultiCmd
        return cast(list[str], subst(template, namespace))

    def _build_namespace(self) -> Namespace:
        """Build a Namespace for variable substitution."""
        tools = self._get_tools()
        vars_dict = self._get_vars()

        # Start with cross-tool variables
        data: dict[str, Any] = dict(vars_dict)

        # $name expands to the environment's name, which lives outside _vars.
        env_name = object.__getattribute__(self, "_name")
        if env_name:
            data["name"] = env_name

        # Add tool namespaces
        for name, config in tools.items():
            data[name] = config.as_namespace()

        return Namespace(data)

    def clone(self, *, name: str | None = None) -> Environment:
        """Create a deep copy of this environment.

        Tool configurations are cloned so modifications don't affect
        the original.

        Args:
            name: A name for the clone. A clone has none otherwise, and
                two environments need names, and different ``build_prefix``
                settings, before they can hold targets with the same name.

        Returns:
            A new Environment with copied configuration.

        Raises:
            PconsError: If *name* is already taken in the project.
        """
        tools = self._get_tools()
        vars_dict = self._get_vars()

        new_env = Environment(defined_at=get_caller_location())

        # Copy cross-tool variables (deep copy lists/dicts)
        new_vars = new_env._get_vars()
        for key, value in vars_dict.items():
            if isinstance(value, list):
                new_vars[key] = value.copy()  # a FlagList keeps its grouping
            elif isinstance(value, dict):
                new_vars[key] = dict(value)
            else:
                new_vars[key] = value

        # Clone tool configurations
        new_tools = new_env._get_tools()
        for tool_name, config in tools.items():
            cloned = config.clone()
            cloned._env = new_env
            new_tools[tool_name] = cloned

        # Rebind BuilderMethod instances to reference the new environment
        # (BuilderMethod stores env reference for node registration)
        from pcons.tools.tool import BuilderMethod

        for config in new_tools.values():
            for var_name in list(config):
                var_value = config.get(var_name)
                if isinstance(var_value, BuilderMethod):
                    # Create new BuilderMethod pointing to new_env
                    config.set(var_name, BuilderMethod(new_env, var_value._builder))

        # Copy applied presets (frozen dataclasses → shallow copy is safe)
        new_env._applied_presets = list(self._applied_presets)
        new_env._applied_imperative = list(self._applied_imperative)
        new_env._intents = list(self._intents)
        new_env._tool_owners = dict(self._tool_owners)
        new_env._use_origins = dict(self._use_origins)

        new_env._build_dir_base = self._build_dir_base
        new_env._build_dir_offset = self._build_dir_offset
        new_env._cross_preset = self._cross_preset
        new_env._target = self._target

        # Copy toolchain references (not cloned - they're shared)
        new_env._toolchain = self._toolchain
        additional: list[Toolchain] = object.__getattribute__(
            self, "_additional_toolchains"
        )
        new_env._additional_toolchains = list(additional)

        # Copy project reference and register with project
        project = object.__getattribute__(self, "_project")
        new_env._project = project
        if project is not None:
            # Register cloned env so its nodes are found by generators
            project._environments.append(new_env)

        # The name isn't copied: two environments with one name would
        # generate the same ninja rule names. The caller may give one.
        new_env._name = None
        if name is not None:
            new_env.name = name

        # Don't copy created_nodes - new environment starts fresh

        return new_env

    @contextmanager
    def override(self, **kwargs: Any) -> Generator[Environment]:
        """Build with a temporarily modified copy of this environment.

        This is :meth:`clone` plus a scope: it yields a full clone, leaving
        the original untouched, and the block shows where the modified
        environment applies. Nothing requires the block — a clone you keep
        and mutate behaves identically, and is the better shape when the
        modified environment outlives one stretch of the build script.

        Modify the clone directly; it is an ordinary Environment, so a flag
        list is an ordinary Python list:

            with env.override() as tuned:
                tuned.cxx.flags.append("-O1")                    # add
                tuned.cxx.flags.remove("-Werror")                # remove
                tuned.cxx.flags = ["-O1"]                        # replace
                project.Program("app", tuned, sources=["main.cpp"])

        Keyword arguments are a shorthand that *assigns*, so they are for
        scalars — ``variant="debug"``, ``cc__cmd="clang"``. Tool attributes
        use ``tool__attr`` notation because Python keywords can't contain a
        dot. Passing a list raises: at a call site ``cxx__flags=["-O1"]``
        reads as "add -O1" but would discard every flag the environment
        already carried, so the operation has to be spelled out in the block
        instead.

        Args:
            **kwargs: Scalar variables or tool settings to assign.

        Yields:
            A clone of this environment with *kwargs* applied.

        Raises:
            TypeError: If a keyword's value is a list, tuple, or other
                sequence — see above.

        Example:
            # Scalars: keyword form
            with env.override(variant="profile", cc__cmd="clang") as profile:
                project.Program("app_profile", profile, sources=["main.cpp"])

            # Lists: modify the clone, so the operation is explicit
            with env.override(variant="debug") as debug_env:
                debug_env.cxx.defines.append("EXTRA_DEBUG")
                debug_env.cxx.flags.extend(["-g3", "-fno-omit-frame-pointer"])
                project.Library("mylib_debug", debug_env, sources=["lib.cpp"])

            # Per-file flags: hand the environment to the sources it applies to
            with env.override() as careful:
                careful.cxx.flags.append("-O1")
                lib.add_sources(["cuda-support.cxx"], env=careful)
        """
        # Validate before cloning or mutating anything, so a rejected call
        # leaves no half-applied environment behind.
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple, UserList)):
                raise TypeError(self._list_override_message(key, value))

        temp_env = self.clone()

        for key, value in kwargs.items():
            if "__" in key:
                # Tool attribute override: cc__cmd -> env.cc.cmd
                tool_name, attr_name = key.split("__", 1)
                tool = temp_env.add_tool(tool_name)  # Returns existing or creates new
                setattr(tool, attr_name, value)
            else:
                # Cross-tool variable override
                setattr(temp_env, key, value)

        yield temp_env

    def _list_override_message(self, key: str, value: Any) -> str:
        """Explain why a list keyword is rejected, and what to write instead.

        Names what the call would discard: that's the part a reader of
        ``override(cxx__flags=["-O1"])`` doesn't see, and the reason the
        keyword form doesn't take lists.
        """
        if "__" in key:
            tool_name, attr_name = key.split("__", 1)
            target = f"{tool_name}.{attr_name}"
            current = None
            if self.has_tool(tool_name):
                current = getattr(getattr(self, tool_name), attr_name, None)
            block = f"e.{target}"
        else:
            target = key
            current = self._get_vars().get(key)
            block = f"e.{target}"

        if isinstance(current, (list, tuple, UserList)) and len(current) > 0:
            shown = ", ".join(repr(v) for v in list(current)[:4])
            if len(current) > 4:
                shown += f", ... ({len(current)} in all)"
            discards = (
                f"would replace {target} entirely, discarding [{shown}].\n"
                f"  Keyword overrides assign; they don't add to a list."
            )
        else:
            discards = (
                f"would assign {target} wholesale.\n"
                f"  Keyword overrides assign; they don't add to a list — and once "
                f"{target} is\n  non-empty this call would silently discard it."
            )

        examples = [
            (f"{block}.append({_first_repr(value)})", "add"),
            (f"{block}.remove(...)", "remove"),
            (f"{block} = {list(value)!r}", "replace outright"),
        ]
        width = max(len(code) for code, _ in examples)
        shown_examples = "\n".join(
            f"          {code:<{width}}  # {what}" for code, what in examples
        )

        return (
            f"env.override({key}=[...]) {discards}\n"
            f"\n"
            f"  Modify a copy of the environment, where the operation is explicit:\n"
            f"      with env.override() as e:      # or: e = env.clone()\n"
            f"{shown_examples}\n"
            f"\n"
            f"  The keyword form is for scalars: "
            f'env.override(variant="debug", cc__cmd="clang").'
        )

    # Convenience methods for common patterns

    def apply(self, preset: Preset) -> None:
        """Apply a :class:`Preset`, recording it for :meth:`explain`.

        Extends each contribution's tool flag/define lists; a ``cmd``
        contribution replaces the tool's command. Presets sharing an
        ``exclusive_group`` act as a knob: applying one first un-applies
        the group's earlier ones. Each toolchain sets the knob for its own
        tools, so a toolchain's preset replaces its own earlier ones
        (``Preset.realized_by``) and any applied directly, and one applied
        directly replaces them all. While add_toolchain() configures the
        toolchain it adds, that toolchain's presets replace only its own:
        the knob the script set stays.
        A preset applies fully or raises (docs/presets.md, "Preset
        application").
        """
        # Validate before any mutation, including the un-apply below.
        self._validate_preset(preset)

        if preset.exclusive_group is not None:

            def replaces(applied: Preset) -> bool:
                if applied.exclusive_group != preset.exclusive_group:
                    return False
                if preset.realized_by is None:
                    return True
                if applied.realized_by is None:
                    return not self._replaying
                return applied.realized_by == preset.realized_by

            for applied in self._applied_presets:
                if replaces(applied):
                    self._unapply_contributions(applied)
            self._applied_presets[:] = [
                p for p in self._applied_presets if not replaces(p)
            ]

        for contribution in preset.contributions:
            self._apply_contribution(contribution)

        self._applied_presets.append(preset)

        if preset.category == "variant":
            self.variant = preset.name
        # Only the set_target_arch knob writes target_arch; on cross
        # presets, arch is metadata.
        if preset.category == "arch" and preset.arch is not None:
            self.target_arch = preset.arch

    def apply_from(self, toolchain: Toolchain, preset: Preset) -> bool:
        """Apply *toolchain*'s *preset* to the tools it owns, and only those.

        Each tool is realized by the toolchain that brought it: gfortran's
        ``-O0 -g`` lands on ``fc``, gcc's on ``cc`` and ``cxx``, and
        neither touches the other's, so nothing is applied twice and no
        toolchain's flag style reaches a tool that can't read it. A tool no
        toolchain brought (the script's own ``env.add_tool``) belongs to the
        primary toolchain, as does a tool the environment lacks, so a
        command swap to a missing tool still raises; with no primary, to
        *toolchain* itself. Whether any of it landed.
        """
        taken = self._owned_contributions(toolchain, preset)
        if not taken and preset.contributions:
            return False
        self.apply(
            dataclasses.replace(preset, contributions=taken, realized_by=toolchain.name)
        )
        return True

    def _owned_contributions(
        self, toolchain: Toolchain, preset: Preset
    ) -> tuple[ToolContribution, ...]:
        """*preset*'s contributions to the tools *toolchain* realizes (see
        apply_from)."""
        default = object.__getattribute__(self, "_toolchain") or toolchain
        return tuple(
            c
            for c in preset.contributions
            if self._tool_owners.get(c.tool, default) is toolchain
        )

    @property
    def applied_presets(self) -> tuple[Preset, ...]:
        """Presets applied to this environment, in order (for inspection)."""
        return tuple(self._applied_presets)

    def explain(self, tool: str | None = None) -> Explanation:
        """Attribute each tool flag/define/command to the preset that set it.

        Args:
            tool: Restrict to a single tool (e.g. "cc"); otherwise all tools.

        Returns:
            An :class:`~pcons.core.explain.Explanation` (printable as a table).

        Example:
            env.set_variant("release")
            env.apply_preset("warnings")
            print(env.explain())       # all tools
            print(env.cc.explain())    # just the C compiler
        """
        from pcons.core.explain import explain as _explain
        from pcons.core.flags import (
            get_passthrough_flags_from_toolchains,
            get_separated_arg_flags_from_toolchains,
        )

        tools = self._get_tools()
        names = [tool] if tool is not None else list(tools.keys())
        snapshot: dict[str, dict[str, Any]] = {}
        for name in names:
            if name not in tools:
                continue
            snapshot[name] = tools[name].as_dict()
        return _explain(
            self._applied_presets,
            snapshot,
            self._applied_imperative,
            get_separated_arg_flags_from_toolchains(self.toolchains),
            get_passthrough_flags_from_toolchains(self.toolchains),
        )

    def _record_toolchain_baseline(
        self, toolchain: Toolchain, tool_names: list[str]
    ) -> None:
        """Record a toolchain's post-setup flags/defines as a 'toolchain' preset,
        so explain() attributes its defaults (e.g. ``/nologo``) to it.
        """
        from pcons.core.preset import Preset, ToolContribution

        tools = self._get_tools()
        contributions: list[ToolContribution] = []
        for name in tool_names:
            tool = tools.get(name)
            if tool is None:
                continue
            flags = tool.get("flags")
            defines = tool.get("defines")
            f = tuple(flags) if isinstance(flags, list) else ()
            d = tuple(defines) if isinstance(defines, list) else ()
            if f or d:
                contributions.append(ToolContribution(name, flags=f, defines=d))
        if contributions:
            self._applied_presets.append(
                Preset(
                    name=toolchain.name,
                    category="toolchain",
                    contributions=tuple(contributions),
                )
            )

    def _validate_preset(self, preset: Preset) -> None:
        """Raise unless *preset* can be applied to this environment."""
        if preset.exclusive_group is not None:
            # Group presets must be invertible (no cmd) to be un-applied.
            cmd_tools = sorted(
                {c.tool for c in preset.contributions if c.cmd is not None}
            )
            if cmd_tools:
                raise ValueError(
                    f"Preset '{preset.name}' is in exclusive group "
                    f"'{preset.exclusive_group}' but replaces the command of "
                    f"tool(s) {', '.join(cmd_tools)}. Group presets switch by "
                    f"un-applying, so they must be purely additive "
                    f"(flags/defines only); model a command swap as a "
                    f"non-grouped preset instead."
                )

        if not preset.contributions:
            # An empty preset is a deliberate no-op.
            return

        tools = self._get_tools()
        missing_cmd = sorted(
            {
                c.tool
                for c in preset.contributions
                if c.cmd is not None and c.tool not in tools
            }
        )
        if missing_cmd:
            raise ValueError(
                f"Preset '{preset.name}' replaces the command of "
                f"tool(s) {', '.join(missing_cmd)}, which this "
                f"environment does not have (available: "
                f"{', '.join(sorted(tools))}). A command override is a "
                f"retargeting mechanism and cannot be dropped silently."
            )
        if not any(c.tool in tools for c in preset.contributions):
            targets = sorted({c.tool for c in preset.contributions})
            raise ValueError(
                f"Preset '{preset.name}' would have no effect: none of "
                f"its target tools ({', '.join(targets)}) exist in this "
                f"environment (available: {', '.join(sorted(tools))})."
            )

    def _unapply_contributions(self, preset: Preset) -> None:
        """Remove one occurrence of each of a preset's flags and defines."""
        tools = self._get_tools()
        for c in preset.contributions:
            tool = tools.get(c.tool)
            if tool is None:
                continue
            flags = tool.get("flags")
            if c.flags and isinstance(flags, list):
                for f in c.flags:
                    if f in flags:
                        flags.remove(f)
            defines = tool.get("defines")
            if c.defines and isinstance(defines, list):
                for d in c.defines:
                    if d in defines:
                        defines.remove(d)

    def _apply_contribution(self, c: ToolContribution) -> None:
        """Apply a single tool contribution (extend flags/defines, set cmd)."""
        if not self.has_tool(c.tool):
            return
        tool = self._get_tools()[c.tool]
        if c.flags:
            flags = tool.get("flags")
            if isinstance(flags, list):
                flags.extend(c.flags)
        if c.defines:
            defines = tool.get("defines")
            if isinstance(defines, list):
                defines.extend(c.defines)
        if c.cmd is not None:
            tool.cmd = c.cmd

    def _record_intent(self, intent: Intent) -> None:
        """Keep *intent* for toolchains added later; one in a group
        replaces the group's earlier one."""
        if intent.group is not None:
            self._intents = [i for i in self._intents if i.group != intent.group]
        self._intents.append(intent)

    def _check_replay(self, toolchain: Toolchain) -> None:
        """Raise now, before *toolchain* changes anything, if it can't
        realize what the script already asked for: an unknown variant, a
        cross target it refuses, or an arch or feature it lacks for the
        compilers it would bring."""
        from pcons.core.preset import is_registered_preset

        tools = getattr(toolchain, "_feature_preset_tools", tuple)()
        takes_part = any(t in toolchain.tools and not self.has_tool(t) for t in tools)
        for intent in self._intents:
            preset = self._preset_for(toolchain, intent)
            if preset is not None or not takes_part:
                continue
            if intent.kind == "arch":
                raise _unrealized_arch(intent.name, [toolchain])
            if intent.kind == "runtime" and intent.name != "shared":
                raise _unrealized_runtime(intent.name, [toolchain])
            if intent.kind == "feature" and not is_registered_preset(intent.name):
                raise _unrealized_feature(intent.name, [toolchain])

    def _takes_part(self, toolchain: Toolchain) -> bool:
        """Whether *toolchain* owns a tool its features and knobs are
        realized on (gfortran's ``fc``), so must realize each one."""
        tools = getattr(toolchain, "_feature_preset_tools", tuple)()
        return any(
            self.has_tool(t) and self.toolchain_for(t) is toolchain for t in tools
        )

    def _realize(
        self,
        intent: Intent,
        toolchains: Sequence[Toolchain],
        *,
        replaying: bool = False,
    ) -> bool:
        """Have each of *toolchains* realize *intent* for its own tools.

        Whether any had a realization. A toolchain that takes part
        (``_takes_part``) and can't realize an arch or feature is an error:
        the intent applies fully or raises. *replaying* is a toolchain added later,
        which must realize what the rest of the environment already does.
        """
        kind = intent.kind
        # Each kind is built for every toolchain first, so one that can't
        # realize it raises before any of them has changed the environment;
        # then each toolchain applies it its own way.
        if kind == "variant":
            for toolchain in toolchains:
                toolchain.make_variant_preset(intent.name, **intent.kwargs)
            for toolchain in toolchains:
                toolchain.apply_variant(self, intent.name, **intent.kwargs)
            # The runtime's debug build goes with the debug variant.
            self._realize(self._runtime_intent(), toolchains, replaying=replaying)
            return True
        if kind == "target":
            cross = intent.args[0]
            for toolchain in toolchains:
                toolchain.make_target_preset(cross)  # raises if it can't
            for toolchain in toolchains:
                toolchain.apply_cross_preset(self, cross)
            # Which runtimes there are depends on the platform built for.
            self._realize(self._runtime_intent(), toolchains, replaying=replaying)
            return True
        if kind == "runtime":
            runtimes = [(t, self._preset_for(t, intent)) for t in toolchains]
            missing = [
                t for t, preset in runtimes if preset is None and self._takes_part(t)
            ]
            # The shared runtimes are every driver's default, so a
            # toolchain with nothing to say about them links them anyway.
            if missing and intent.name != "shared":
                raise _unrealized_runtime(intent.name, missing)
            # Realized whether or not the script asked, so for the tools
            # the environment has.
            for toolchain, preset in runtimes:
                if preset is not None:
                    present = tuple(
                        c for c in preset.contributions if self.has_tool(c.tool)
                    )
                    self.apply_from(
                        toolchain, dataclasses.replace(preset, contributions=present)
                    )
            return True
        if kind == "arch":
            arches = [(t, t.make_arch_preset(intent.name)) for t in toolchains]
            realized = [t for t, preset in arches if preset is not None]
            missing = [
                t for t, preset in arches if preset is None and self._takes_part(t)
            ]
            if missing and (replaying or realized):
                raise _unrealized_arch(intent.name, missing)
            for toolchain in realized:
                toolchain.apply_target_arch(self, intent.name, **intent.kwargs)
            return bool(realized)
        if kind == "setting":
            realized = False
            for toolchain in toolchains:
                preset = self._preset_for(toolchain, intent)
                if preset is not None:
                    self.apply_from(toolchain, preset)
                    realized = True
            return realized
        if kind == "feature":
            return self._realize_feature(intent, toolchains, replaying=replaying)
        raise ValueError(f"Unknown intent kind '{kind}'")

    def _realize_feature(
        self, intent: Intent, toolchains: Sequence[Toolchain], *, replaying: bool
    ) -> bool:
        """The feature part of :meth:`_realize`. Nothing is applied unless
        every toolchain that takes part has a realization."""
        from pcons.core.preset import is_registered_preset

        name = intent.name
        found: list[tuple[Toolchain, Preset]] = []
        missing: list[Toolchain] = []
        for toolchain in toolchains:
            preset = self._preset_for(toolchain, intent)
            if preset is not None:
                found.append((toolchain, preset))
            elif not is_registered_preset(name) and self._takes_part(toolchain):
                # A registered preset's None is its resolver's decision.
                missing.append(toolchain)
        if missing and (found or replaying):
            raise _unrealized_feature(name, missing)
        landed = [self.apply_from(toolchain, preset) for toolchain, preset in found]
        if found and not any(landed) and not replaying:
            owners = sorted({c.tool for _, p in found for c in p.contributions})
            raise ValueError(
                f"Preset '{name}' would have no effect: its flags are for "
                f"{', '.join(owners)}, which other toolchains in this "
                f"environment own."
            )
        return bool(found)

    def _preset_for(self, toolchain: Toolchain, intent: Intent) -> Preset | None:
        """*toolchain*'s realization of *intent*, without applying it."""
        from pcons.core.preset import resolve_registered_feature

        kind = intent.kind
        if kind == "variant":
            return toolchain.make_variant_preset(intent.name, **intent.kwargs)
        if kind == "arch":
            return toolchain.make_arch_preset(intent.name)
        if kind == "target":
            return toolchain.make_target_preset(intent.args[0])
        if kind == "feature":
            preset = toolchain.make_feature_preset(intent.name, self.target)
            if preset is None:
                preset = resolve_registered_feature(intent.name, toolchain)
            return preset
        if kind == "setting":
            return toolchain.make_setting_preset(intent.name, *intent.args)
        if kind == "runtime":
            return toolchain.make_runtime_preset(intent.name, self.variant, self.target)
        raise ValueError(f"Unknown intent kind '{kind}'")

    def flags_realized_by(self, tool: str, toolchain: Toolchain) -> list[Any]:
        """*tool*'s flags as they'd be were *toolchain* its realizer.

        A target whose link another toolchain's driver runs (gfortran's,
        beside gcc) takes that toolchain's realization of the variant,
        features and cross target: the flags the owner realized come out,
        and *toolchain*'s for the same intents go in. Flags the script or a
        package put there stay.
        """
        primary = object.__getattribute__(self, "_toolchain")
        flags: list[Any] = list(getattr(self, tool).flags or ())
        if self._tool_owners.get(tool, primary) is toolchain:
            return flags
        owner = self._tool_owners.get(tool, primary)
        for preset in self._applied_presets:
            if owner is not None and preset.realized_by == owner.name:
                for c in preset.contributions:
                    if c.tool == tool:
                        for flag in c.flags:
                            if flag in flags:
                                flags.remove(flag)
        presets = [
            *toolchain.setup_presets(self),
            *(self._preset_for(toolchain, intent) for intent in self._intents),
        ]
        for preset in presets:
            for c in preset.contributions if preset is not None else ():
                if c.tool == tool:
                    flags.extend(c.flags)
        return flags

    def feature_realized(self, name: str, toolchain: Toolchain) -> bool:
        """Whether feature preset *name* was applied here, so *toolchain*'s
        objects carry it and may need its runtime from a link."""
        return any(i.kind == "feature" and i.name == name for i in self._intents)

    def _apply_setting(self, name: str, *args: Any) -> None:
        """Apply a tool setting (``env.cxx.set_standard``), realized by
        each toolchain that has one by that name."""
        from pcons.core.preset import Intent

        intent = Intent("setting", name, args)
        self._realize(intent, self.toolchains)
        self._record_intent(intent)

    def set_variant(self, name: str, **kwargs: Any) -> None:
        """Set the build variant; each toolchain translates the name to flags.

        Args:
            name: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options passed to apply_variant().

        Example:
            env.set_variant("debug")
            env.set_variant("release", extra_flags=["-march=native"])
        """
        from pcons.core.preset import VARIANT_GROUP, Intent

        trace("env", "Setting variant: %s", name)
        # Recorded whether or not a toolchain realizes it, because this is the
        # only point at which a variant name is observable: `get_variant` takes
        # a string and returns one. The CLI persists the names for completion.
        _record_variant(name)
        intent = Intent("variant", name, kwargs=dict(kwargs), group=VARIANT_GROUP)
        if self.toolchains:
            self._realize(intent, self.toolchains)
        else:
            # No toolchains - just set the variant name
            self.variant = name
        self._record_intent(intent)

    def set_target_arch(self, arch: str, **kwargs: Any) -> None:
        """Set the target CPU arch; each toolchain translates the name to flags.

        Raises if no configured toolchain can retarget to *arch* (e.g. on
        Linux, where retargeting needs a cross preset or cross toolchain).

        Args:
            arch: Architecture name (e.g., "arm64", "x86_64", "x64").
            **kwargs: Toolchain-specific options passed to apply_target_arch().

        Example:
            env.set_target_arch("arm64")  # -arch on macOS, /MACHINE: on MSVC
        """
        from pcons.core.preset import Intent

        if self.toolchains:
            intent = Intent("arch", arch, kwargs=dict(kwargs))
            if not self._realize(intent, self.toolchains):
                names = ", ".join(t.name for t in self.toolchains)
                raise ValueError(
                    f"No configured toolchain ({names}) realizes target "
                    f"arch '{arch}'. Retargeting the CPU on this platform "
                    f"may need a cross toolchain or cross preset instead "
                    f"(see docs/presets.md)."
                )
            self._record_intent(intent)
        else:
            self.target_arch = arch

    def set_runtime_linkage(self, linkage: str) -> None:
        """Link the language runtimes (the C and C++ runtimes, Fortran's)
        shared, the default, or static.

        Each toolchain links its own runtime that way: ``/MT`` in place of
        ``/MD`` for an MSVC-ABI compiler (``/MTd`` under ``debug``),
        ``-static-libgcc -static-libstdc++`` for GCC. A toolchain that
        can't link its runtime that way is an error: clang on Apple's
        platforms can't link it statically.

        Args:
            linkage: "shared" or "static".

        Example:
            env.set_runtime_linkage("static")
        """
        from pcons.core.preset import RUNTIME_LINKAGE_GROUP, RUNTIME_LINKAGES, Intent

        if linkage not in RUNTIME_LINKAGES:
            raise ValueError(
                f"Unknown runtime linkage '{linkage}': it is "
                f"{' or '.join(repr(k) for k in RUNTIME_LINKAGES)}."
            )
        intent = Intent("runtime", linkage, group=RUNTIME_LINKAGE_GROUP)
        self._realize(intent, self.toolchains)
        self._record_intent(intent)

    @property
    def runtime_linkage(self) -> str:
        """How the language runtimes are linked: "shared" or "static"
        (see :meth:`set_runtime_linkage`)."""
        return self._runtime_intent().name

    def _runtime_intent(self) -> Intent:
        return next(i for i in reversed(self._intents) if i.kind == "runtime")

    def use_compiler_cache(self, tool: str | None = None) -> None:
        """Run the compile commands behind a compiler cache.

        Sets ccache or sccache as the launcher on the cc and cxx tools; the
        linker and archiver are left alone, having nothing to cache. Which
        caches exist, and their quirks, live in
        :mod:`pcons.tools.compiler_cache`.

        Args:
            tool: "ccache", "sccache", or None for auto-detect.
                  Auto-detect tries sccache first, then ccache.
        """
        from pcons.tools.compiler_cache import apply_compiler_cache

        apply_compiler_cache(self, tool)

    def use_clang_tidy(self, tool: str | None = None, args: Sequence[str] = ()) -> None:
        """Run clang-tidy alongside every C and C++ compile.

        Each compile first runs clang-tidy on its source with the compile's
        own flags, then compiles. The compile runs either way, so the object
        still appears; what fails the build is clang-tidy's exit status, so a
        warning does not and an error does. ``--warnings-as-errors=*`` in
        *args* makes warnings fail it too. The driver is
        :mod:`pcons.tools.co_compile`. Composes with ``use_compiler_cache()``
        in either order.

        Args:
            tool: The clang-tidy program, by name or path. None looks for
                  "clang-tidy" in PATH; a missing one is a warning and the
                  compiles are left alone.
            args: Extra clang-tidy arguments, e.g. ``["--use-color"]`` or
                  ``["--warnings-as-errors=*"]``. Checks are best set in a
                  ``.clang-tidy`` file, which clang-tidy finds by itself.
        """
        from pcons.tools.co_compile import apply_clang_tidy

        apply_clang_tidy(self, tool, args)

    def apply_preset(self, name: KnownFeaturePreset | str) -> None:
        """Apply a named feature preset to this environment.

        Resolution is **toolchain-first, then registry**: each toolchain's
        built-in ``FEATURE_PRESETS`` is tried first, then the contributed-preset
        registry (see :func:`pcons.register_preset`). Built-ins use bare names
        (``warnings``, ``werror``, ``sanitize``, ``lto``, ``hardened``);
        contributed presets are namespaced (``scope/name``). ``explain()``
        attributes each flag to the preset that added it.

        Every toolchain whose compilers the preset covers must realize it,
        or this raises: with gcc and gfortran, ``sanitize`` sanitizes both
        the C and the Fortran sources or neither. To leave some sources out,
        compile them in a clone of the environment made before the preset
        (``target.add_sources(sources, env=clone)``).

        Args:
            name: Preset name (``"warnings"`` or ``"scope/name"``).

        Example:
            env.apply_preset("warnings")
            env.apply_preset("mycorp/strict")
        """
        from pcons.core.preset import (
            Intent,
            apply_imperative_preset,
            is_registered_preset,
        )

        if not self.toolchains:
            logger.warning("No toolchains configured; cannot apply preset '%s'", name)
            return

        intent = Intent("feature", name)
        applied = self._realize(intent, self.toolchains)
        # Imperative escape-hatch preset: runs once against the whole env.
        description = apply_imperative_preset(name, self)
        if description is not None:
            self._applied_imperative.append((name, description))
            applied = True
        # A registered preset that resolved to None is a deliberate no-op;
        # an unrecognized name is an error.
        if not applied and not is_registered_preset(name):
            available = sorted(
                {
                    p
                    for toolchain in self.toolchains
                    for p in getattr(toolchain, "FEATURE_PRESETS", {})
                }
            )
            if name in available:
                # The name is declared but its realization came back empty:
                # an optional feature (e.g. openmp) this system can't provide.
                raise ValueError(
                    f"Preset '{name}' is not available with this toolchain on "
                    f"this system. Optional features can be guarded with "
                    f"env.has_preset({name!r})."
                )
            raise ValueError(
                f"Unknown preset '{name}'. Toolchain built-ins here: "
                f"{', '.join(available) or '(none)'}; contributed presets "
                f"are listed by pcons.list_presets()."
            )
        self._record_intent(intent)

    def has_preset(self, name: KnownFeaturePreset | str) -> bool:
        """Whether ``apply_preset(name)`` would apply here, not raise.

        True if the toolchains realize *name* (built-in or contributed
        declarative): every one whose compilers it covers, or *name* is a
        registered imperative preset. False for unknown names, for
        optional features this system can't provide (e.g. ``openmp`` when
        no OpenMP runtime is available), and for a feature one of the
        environment's compilers lacks (``sanitize`` with flang beside
        clang) — checking never raises.

        Use it to guard optional features, mirroring CMake's optional
        ``find_package``::

            if env.has_preset("openmp"):
                env.apply_preset("openmp")

        Example:
            env.has_preset("openmp")   # True where OpenMP can be enabled
            env.has_preset("pthread")  # False on MSVC
        """
        from pcons.core.preset import (
            is_imperative_preset,
            is_registered_preset,
            resolve_registered_feature,
        )

        if is_imperative_preset(name):
            return True
        found = lands = False
        for toolchain in self.toolchains:
            preset = toolchain.make_feature_preset(name, self.target)
            if preset is None:
                preset = resolve_registered_feature(name, toolchain)
            if preset is not None:
                found = True
                lands = lands or (
                    not preset.contributions
                    or bool(self._owned_contributions(toolchain, preset))
                )
            elif not is_registered_preset(name) and self._takes_part(toolchain):
                return False
        return found and lands

    def apply_cross_preset(self, preset: Any) -> None:
        """Apply a cross-compilation preset to this environment.

        Cross-compilation presets configure sysroot, target triple,
        architecture flags, and SDK paths for building on a different
        platform.

        Args:
            preset: A CrossPreset dataclass instance.

        Example:
            from pcons.toolchains.presets import android, ios

            env.apply_cross_preset(android(ndk="~/android-ndk", api=35))
            env.apply_cross_preset(ios(arch="arm64"))
        """
        from pcons.core.preset import Intent

        if self.toolchains:
            target = getattr(preset, "target_platform", None)
            self._check_retarget(target)
            intent = Intent("target", getattr(preset, "name", "target"), (preset,))
            # Set first: the runtime linkage, realized again with it, depends
            # on the platform built for.
            previous = (self._cross_preset, self._target)
            self._cross_preset = preset
            self._target = target
            try:
                self._realize(intent, self.toolchains)
            except Exception:
                self._cross_preset, self._target = previous
                raise
            self._record_intent(intent)
        else:
            logger.warning(
                "No toolchains configured; cannot apply cross-preset '%s'",
                preset.name if hasattr(preset, "name") else preset,
            )

    def _check_retarget(self, target: Platform | None) -> None:
        """Raise if a feature already applied has other flags for *target*
        (None: the build machine): they were realized for this one."""
        from pcons.configure.platform import get_platform

        new = target or get_platform()
        if new == self.target:
            return
        for intent in self._intents:
            if intent.kind != "feature":
                continue
            for toolchain in self.toolchains:
                old = toolchain.make_feature_preset(intent.name, self.target)
                if old != toolchain.make_feature_preset(intent.name, new):
                    raise ValueError(
                        f"Apply the cross preset before preset '{intent.name}', "
                        f"whose flags depend on the platform built for."
                    )

    @property
    def cross(self) -> Any:
        """The cross preset this environment was retargeted with, or None.

        None means "nothing said otherwise", never "this is a host build": an
        environment retargeted by hand, by setting tool commands without a
        preset, records nothing, and so does every ordinary build. A reader
        must default to the host on None and may never refuse on it.

        A second ``apply_cross_preset`` replaces the first: an environment has
        one target, and the flags of the later preset are the ones that end up
        deciding it.
        """
        preset: Any = self._cross_preset
        return preset

    @property
    def target(self) -> Platform:
        """The platform this environment builds for.

        The host platform unless something said otherwise: a cross preset
        whose triple names a platform, or one carrying an explicit one. An
        environment retargeted by hand, by setting tool commands with no
        preset, says nothing and so reads as the host.

        This answers "what was declared", never "what will actually run", so
        a caller may not treat a host answer as proof of a host build.
        """
        from pcons.configure.platform import get_platform

        return self._target or get_platform()

    def Glob(self, pattern: str) -> list[FileNode]:
        """Find files matching a glob pattern.

        This is a placeholder - actual implementation will use
        the project's file tracking.

        Args:
            pattern: Glob pattern (e.g., 'src/*.cpp').

        Returns:
            List of FileNodes matching the pattern.
        """
        from pcons.core.node import FileNode

        # Use project._node() for deduplication when available
        matches = list(Path(".").glob(pattern))
        if self._project is not None:
            return [self._project._node(p) for p in matches]
        return [FileNode(p, defined_at=get_caller_location()) for p in matches]

    def Framework(self, *names: str, dirs: list[str] | None = None) -> None:
        """Add macOS frameworks to link against.

        This is a convenience method for adding frameworks to the linker.
        It modifies env.link.frameworks and optionally env.link.frameworkdirs.

        On non-macOS platforms, this method still adds the frameworks to the
        environment variables (for cross-compilation scenarios), but they
        will have no effect when building on those platforms.

        Args:
            *names: Framework names (e.g., "Foundation", "CoreFoundation").
            dirs: Optional list of framework search directories.

        Example:
            # Add single framework
            env.Framework("Foundation")

            # Add multiple frameworks
            env.Framework("Foundation", "CoreFoundation", "Metal")

            # Add framework with custom search path
            env.Framework("MyFramework", dirs=["/path/to/frameworks"])
        """
        if not self.has_tool("link"):
            return

        link = self.link
        if "frameworks" not in link:
            link.set("frameworks", [])
        if "frameworkdirs" not in link:
            link.set("frameworkdirs", [])

        for name in names:
            if name not in link.frameworks:
                link.frameworks.append(name)

        if dirs:
            for d in dirs:
                if d not in link.frameworkdirs:
                    link.frameworkdirs.append(d)

    def use(self, package: Any, *, system: bool = False) -> None:
        """Apply a package's settings to this environment.

        This is the preferred way to use external packages. It applies all
        compile and link settings from a PackageDescription or ImportedTarget.

        The package's settings are applied to the appropriate tools:
        - include_dirs → cxx.includes (and cc.includes, fc.includes if
          present)
        - defines → cxx.defines (and cc.defines, fc.defines if present)
        - compile_flags → the compilers of the package's languages: C and
          C++ (cc, cxx), unless it declares others
          (``PackageDescription.languages``)
        - library_dirs → link.libdirs
        - libraries → link.libs
        - link_flags → link.flags
        - frameworks → link.frameworks (macOS)
        - framework_dirs → link.frameworkdirs (macOS)

        Args:
            package: A PackageDescription, ImportedTarget, or any object with
                    include_dirs, defines, libraries, etc. attributes.
            system: If True, the package's include directories are applied as
                   system includes (-isystem, /external:I), so warnings from
                   its headers are suppressed. The package is left unchanged.

        Example:
            # Find and use a package
            pkg = finder.find("fmt")
            env.use(pkg)

            # Third-party headers, held to no warning set of ours
            env.use(finder.find("doctest"), system=True)

            # Or with ImportedTarget
            target = ImportedTarget.from_package(pkg)
            env.use(target)

            # Multiple packages
            for pkg in [fmt_pkg, spdlog_pkg]:
                env.use(pkg)
        """
        from pcons.tools.requirements import apply_requirements_to_env

        if hasattr(package, "public"):
            # A Target's public requirements are already UsageRequirements.
            reqs = package.public
            imported = getattr(package, "is_imported", False)
            languages = set(package.required_languages) if imported else set()
        else:
            from pcons.packages.imported import requirements_from_package

            reqs = requirements_from_package(package)
            languages = set(getattr(package, "languages", ()) or ())

        if system:
            # Clone first: system= describes this use, not the package, which
            # may be used unsystem'd elsewhere.
            reqs = reqs.clone()
            reqs.make_includes_system()

        apply_requirements_to_env(
            self, reqs, origin=getattr(package, "name", None), languages=languages
        )

    def _resolve_cwd(self, cwd: str | Path | None) -> Path | None:
        """Anchor a ``cwd=`` argument, which is relative to the declaring
        script's directory, as ``sources=`` is.

        Absolute at this point, so generators need no notion of where a
        working directory was spelled from.
        """
        if cwd is None:
            return None
        path = Path(cwd)
        if path.is_absolute():
            return path
        from pcons.core.project import Project

        if Project.has_current():
            return Project.current().current_dir / path
        root = getattr(self._project, "root_dir", None) if self._project else None
        return (root or Path.cwd()) / path

    def Command(
        self,
        *,
        target: CommandTargets | Callable[[], CommandTargets],
        tool: Target | str | Path | None = None,
        source: CommandSources | Callable[[], CommandSources] | None = None,
        command: str | Sequence[CommandToken | Callable[[], CommandToken]] = "",
        name: str | None = None,
        depends: Target | str | Path | Sequence[Target | str | Path] | None = None,
        restat: bool = False,
        write_if_different: bool = False,
        cwd: str | Path | None = None,
        launcher: Sequence[str] | None = None,
        env_vars: Mapping[str, str] | None = None,
        worker: Any = None,
        depfile: str | Path | None = None,
        deps_style: str | None = None,
    ) -> Target:
        """Run an arbitrary shell command to build targets from sources.

        This is a general-purpose builder for running shell commands that
        don't fit into the standard compile/link model. It supports variable
        substitution for common patterns.

        **BREAKING CHANGE (v0.2.0):** This method now returns a `Target` object
        instead of `list[FileNode]`, and uses keyword-only arguments. To access
        output nodes, use `target.output_nodes`.

        Args:
            target: Output file(s) that the command produces. Paths are
                    relative to the build directory of the script that
                    declares them: in a subdirectory reached through
                    ``add_subdirectory``, ``"out.txt"`` is
                    ``<build_dir>/<subdir>/out.txt``, where that script's
                    programs and libraries also build. A relative path is
                    taken as written, so ``"build/out.txt"`` is a ``build``
                    subdirectory. An absolute path is that file exactly:
                    ``env.build_dir / "out.txt"`` is ``"out.txt"``, since
                    ``env.build_dir`` is where a relative target lands. An
                    absolute path outside the build directory is an external
                    output, produced in place.

                    A callable taking no arguments stands for a file this
                    edge's dependencies decide: it's called during resolve,
                    after they have resolved, and what it returns is anchored
                    as a written-out target is. So an edge can name its output
                    after a program's real file name, which only the linker's
                    naming settles. Whatever the callable reads has to be a
                    dependency of this edge (``depends=``, ``tool=``, or a
                    ``source=``), or nothing orders it first. Until then the
                    target has no ``output_nodes``, and an unnamed one is
                    labelled by its output only once it has one.
            tool: The program that runs this command, written ``$TOOL`` in
                    *command*. A ``Target`` becomes an implicit dependency,
                    never a source, so ``$SOURCES`` keeps meaning what the
                    command consumes and the indices a caller wrote keep
                    their files. It is spelled the way the shell will run
                    it, so no ``./`` is written by hand and the same script
                    works on every platform. A tool from another environment
                    carries that environment's own output directory, which
                    is what makes a host tool usable in a cross build.

                    A ``str`` or ``Path`` is for a tool this build does not
                    produce: an absolute path to an installed program, or a
                    bare name looked up on ``$PATH``. Neither adds a
                    dependency, since there is nothing to build.

                    This is the convenience form for naming a program by
                    string. A command written as a list can name its program
                    directly, by putting the ``Target`` or ``FileNode``
                    first, which spells it the same way; where both are
                    given, index 0 is what runs.
            source: Input file(s) that the command depends on. Can be Targets
                   (whose output files become sources), paths, or None. A
                   relative path is read from the directory of the script
                   that declares it, like a target's ``sources=``. A
                   callable, as ``target=`` takes one, is called during
                   resolve and returns the whole list; a Target in it is
                   resolved first, like a written-out one.
            command: The shell command to run. Supports variable substitution:
                    - $SOURCE / $SOURCES: All source files (space-separated);
                      the two spellings mean the same thing
                    - $TARGET / $TARGETS: All target files (space-separated)
                    - ${SOURCES[n]}: Indexed source access (0-based)
                    - ${TARGETS[n]}: Indexed target access (0-based)
                    - ${SOURCES[n:m]}: A range of sources, either end optional
                    - $SRCDIR: The declaring script's source directory
                      (the project root, at the top level). Use this
                      to reference source-tree files that aren't listed as
                      sources (e.g., config files, scripts). Example:
                      "$SRCDIR/scripts/generate.py $SOURCE $TARGET"
                    - $$: A literal dollar sign, delivered to the command
                      verbatim (the shell does not expand it). A variable
                      for the command's own environment belongs in
                      ``env_vars=``, not in the command line.
                    Any of these may be part of a larger argument, e.g.
                    "--out=$TARGET". Attached to a form that expands to
                    several paths, the text repeats on each of them.
                    Any other $variable is expanded from this environment.

                    In the list form, a token may be a ``Target`` or a
                    ``FileNode`` instead of a string: it expands to that
                    output's path, as seen from where the command runs, and
                    becomes an implicit dependency of this command, so the
                    build produces it first and re-runs when it changes. It
                    does not join ``$SOURCES``, so the indices a caller
                    already wrote keep their meaning. A ``Target`` with
                    several outputs is ambiguous and raises; name the one
                    that is meant, ``tool.output_nodes[0]``. A ``Path``
                    names a file the same way, read like ``sources=`` when
                    relative, but adds no dependency: it may be a directory
                    the command only writes to.

                    A token may also be a callable taking no arguments, for
                    something only resolve knows, as ``target=`` takes one:
                    it's called then, and returns a str, a Path or a file
                    node, taken as if written there.

                    First in the list, such a token is the program the
                    command runs, and is spelled the way the shell will run
                    it, exactly as ``tool=`` is: no ``./`` written by hand
                    and no platform conditional. Anywhere else it is an
                    argument and stays a plain path. Passing ``tool=`` as
                    well leaves index 0 the program; ``$TOOL`` is then one
                    more path on the line.

                    **The command runs in the build directory**, unlike
                    ``sources=`` (read from the declaring script's
                    directory) and ``target=`` (written to its build
                    directory). So a path
                    written relative — "tools/gen.pl" — is looked for under
                    the build directory and won't be found. Spell it
                    "$SRCDIR/tools/gen.pl" (``$SRCDIR`` is the declaring
                    script's directory, like ``sources=``), pass an absolute path
                    (pcons rewrites those to stay relocatable), or move the
                    whole command with ``cwd=``.

                    Do not quote a token yourself: pcons keeps the command as
                    tokens and quotes each for the shell it writes for, so
                    hand-quoting arrives at the program with the quotes still
                    attached, and a token starting with a quote raises. A
                    token that must contain a space goes in the list form,
                    which isn't split on whitespace; one whose quotes really
                    are meant goes in ``Verbatim(...)``.
            name: Optional name for this target. Give one to refer to it by
                  name later: ``get_target()``, ``Default()``, ``pcons
                  build``, and ``sub::name@env`` from another build script.
                  It must then be unique within its environment and project.
                  Leave it out and the target needs no name.
            depends: Extra files that trigger a rebuild when changed, but
                    don't appear in $SOURCE/$SOURCES. These become implicit
                    dependencies (after ``|`` in ninja). Useful for scripts,
                    config files, or other build-time inputs.
            restat: If True, Ninja will re-check the output timestamp after
                   running the command. If the output didn't actually change,
                   downstream targets won't be rebuilt. Useful for code
                   generators that may produce identical output.
            write_if_different: If True, restore any output the command
                   rewrote with identical content, timestamp included, and
                   set ``restat``. This is what makes ``restat`` pay off for
                   a generator that unconditionally rewrites its outputs —
                   without it, one changed input rebuilds everything
                   downstream of every output. See
                   ``pcons.tools.stable_output``.
            cwd: Directory to run the command in. Build tools run from the
                   build directory; a tool that insists on the source tree
                   (reads a data file by a path relative to it, writes
                   beside its inputs) needs this. A relative path is taken
                   from the project root. Every path this command's edge
                   names is emitted as seen from *cwd*, so ``$SOURCE``,
                   ``$TARGET`` and ``$SRCDIR`` keep working; the generated
                   build file stays as relocatable as it was. Use this
                   rather than writing ``cd ... &&`` into the command,
                   which would also strand the ``write_if_different``
                   wrapper (see ``pcons.tools.stable_output``).
            launcher: Program to run this command behind, as tokens --
                   ``["valgrind", "-q"]``, a persistent-worker client. Unlike
                   a launcher on a tool namespace (``env.cc.launcher``), which
                   follows every edge that tool runs, this one applies to this
                   command alone. See :mod:`pcons.core.launcher`.
            env_vars: Environment variables for this command alone, e.g.
                   ``{"SIGNING_URL": url}``. Rendered as the innermost
                   launcher (``env NAME=VALUE`` on POSIX, the pcons ``env``
                   helper command on Windows), so the variables ride the
                   generated build file: they survive a direct ``ninja`` run
                   and are seen by no other command. Values expand ``$var``
                   references from this environment, like any launcher token;
                   the per-edge ``$TARGET``/``$SOURCE`` forms are not
                   available here. Setting ``os.environ``
                   in the build script instead would reach every command in
                   the build, and only when pcons itself runs the tool.
            worker: A :class:`pcons.workers.Worker` to run this command in,
                   for an action that costs more to start than to run.
                   Renders to a launcher, so the generated build file still
                   builds standalone: with no worker listening, the command
                   runs directly. See :mod:`pcons.workers`.
            depfile: The make-style dependency file the command writes.
                   A suffix names it after the output: ".d" promises the
                   command writes its discovered dependencies to
                   ``<target>.d``. A path, written like ``target=``, is for
                   a tool that names the file itself. Whatever that file
                   lists is rebuilt against, so a generator that reads
                   includes or imports keeps working after an input it
                   wasn't told about changes. Only for a command with a
                   single target. Ninja deletes the file once it has read
                   it.
            deps_style: How those dependencies arrive: "gcc" (the default),
                   the make-style depfile above, or "msvc", MSVC
                   ``/showIncludes`` lines on stdout. Only meaningful
                   alongside ``depfile``.

        Returns:
            Target object representing the command outputs.

        Example:
            # Generate a header from a template
            generated = env.Command(
                target="config.h",
                source=["config.h.in", "version.txt"],
                command="python generate_config.py $SOURCES > $TARGET"
            )

            # Run a code generator with multiple outputs
            parser = env.Command(
                target=["parser.c", "parser.h"],
                source="grammar.y",
                command="bison -d -o ${TARGETS[0]} $SOURCE"
            )

            # Command with no source dependencies
            timestamp = env.Command(
                target="timestamp.txt",
                source=None,
                command="date > $TARGET"
            )

            # Use another target's output as source
            app = project.Program("app", env, sources=["main.cpp"])
            pkg = env.Command(
                target="app.pkg",
                source=[app],
                command="pkgbuild --root $SOURCE $TARGET"
            )

            # Run a tool this build produced over a variable-length input
            # list. $SOURCES stays the real inputs, and nothing writes a
            # "./" or a platform conditional.
            atlas = env.Command(
                target="atlas.bin",
                tool=packer,
                source=sprites,
                command="$TOOL --out=$TARGET $SOURCES",
            )

            # Can be passed to Install() since it's a Target
            project.Install("dist/", [generated])
        """
        from pcons.core.builder import (
            GenericCommandBuilder,
            anchor_target_paths,
            output_label,
        )
        from pcons.core.errors import PconsError
        from pcons.core.node import FileNode
        from pcons.core.target import Target as TargetClass

        # Taken once: a callable target= or source= runs during resolve, when
        # nothing but pcons's own frames is on the stack to blame.
        defined_at = get_caller_location()
        # A callable stands for files this edge's dependencies decide, so it
        # is called during resolve rather than here; see make_nodes below.
        lazy_target = callable(target)
        lazy_source = callable(source)

        command_deps: list[TargetClass | FileNode] = []
        if not isinstance(command, str):
            command = list(command)
            command_deps = [
                token for token in command if isinstance(token, (TargetClass, FileNode))
            ]

        if depfile is not None and not _is_depfile_suffix(depfile):
            depfile = anchor_target_paths(self, [depfile])[0]
        if deps_style is not None and deps_style not in ("gcc", "msvc"):
            raise PconsError(
                f'deps_style={deps_style!r}: expected "gcc" (a make-style '
                f'depfile) or "msvc" (/showIncludes output).',
                location=defined_at,
            )
        if depfile is None:
            if deps_style is not None:
                raise PconsError(
                    f"deps_style={deps_style!r} needs depfile= as well: it "
                    f"says how the dependencies the command writes arrive, "
                    f"and without a depfile it writes none.",
                    location=defined_at,
                )
        elif deps_style is None:
            deps_style = "gcc"

        # Create the builder
        # A worker is a launcher with a lifecycle; the edge only ever sees
        # the tokens that route it through one.
        if worker is not None:
            launcher = [*(launcher or []), *worker.launcher()]
        # Innermost, after any worker client: the variables must reach the
        # command itself, not the wrappers in front of it.
        if env_vars:
            from pcons.core.launcher import env_vars_launcher

            launcher = [*(launcher or []), *env_vars_launcher(env_vars)]

        builder = GenericCommandBuilder(
            command,
            restat=restat or write_if_different,
            cwd=self._resolve_cwd(cwd),
            launcher=launcher,
            depfile=depfile,
            deps_style=deps_style,
        )

        from pcons.core.subst import ToolPath

        names_tool = any(isinstance(t, ToolPath) for t in builder.command)
        if names_tool and tool is None:
            raise PconsError(
                "$TOOL in a command names the program tool= gives it, and "
                "this command has no tool=.",
                location=defined_at,
            )
        if tool is not None and not names_tool:
            raise PconsError(
                f"tool={tool!r} is never run: the command has no $TOOL. "
                f"Write it where the program goes, or drop tool= and list "
                f"the tool under source= or depends=.",
                location=defined_at,
            )

        cmd_target = TargetClass(
            name or "command",
            target_type="command",
            defined_at=defined_at,
            env=self,
            anonymous=name is None,
        )
        cmd_target._builder_name = "Command"
        # A command makes a product, so a plain `ninja` builds it. A script
        # that wants it kept out of the ordinary build says so:
        # `cmd.build_tier = "all"` (see pcons.core.tiers).
        cmd_target.place_in_tier("default", by="Command")

        def make_nodes() -> None:
            """Anchor this edge's targets and make its nodes.

            Runs at the call for an ordinary command, and during resolve when
            ``target=`` or ``source=`` is a callable, once the resolver has
            settled this edge's dependencies, so a callable may name a file
            after what one of them builds. Either way the anchoring, the
            source normalization and the node registration are the same,
            done in the directory the command was declared in.
            """
            given_target = cast(
                "CommandTargets", target() if callable(target) else target
            )
            given_source = cast(
                "CommandSources | None", source() if callable(source) else source
            )
            if lazy_target and not _as_list(given_target):
                raise PconsError(
                    f"the target= callable returned {given_target!r}. It must "
                    f"return the file, or files, the command writes.",
                    location=defined_at,
                )

            # Separate Targets from immediate sources. A Target's outputs
            # don't exist until it resolves, so it can't become a node here,
            # but its *position* has to survive, or $SOURCE and ${SOURCES[n]}
            # refer to different files than the script wrote.
            source_list: list[Any] = _as_list(given_source)
            immediate_sources = [
                src for src in source_list if not isinstance(src, TargetClass)
            ]
            target_sources = [
                src for src in source_list if isinstance(src, TargetClass)
            ]
            _warn_if_source_reads_as_singular(command, len(source_list), defined_at)

            # Nodes up front, so the declared order below can splice Targets
            # back into their positions; the builder passes existing nodes
            # through. Then the ordinary builder entry point, which anchors the
            # targets and normalizes the sources as it does for every builder.
            normalized = builder._normalize_sources(immediate_sources, self)
            nodes = builder(self, given_target, list(normalized), defined_at=defined_at)
            outputs = [node for node in nodes if isinstance(node, FileNode)]
            for node in outputs:
                self.register_node(node)
                cmd_target.output_nodes.append(node)
            # An anonymous command wears its first output's path; one that
            # declares no output keeps the placeholder.
            if name is None and outputs:
                cmd_target.name = output_label(self, outputs[0].path)

            if target_sources:
                cmd_target._add_pending_sources(target_sources)
                # The declared sequence, with each Target still a Target: the
                # factory substitutes its outputs in place once they exist,
                # so $SOURCES keeps the order the script wrote instead of
                # listing every Target last.
                normalized_iter = iter(normalized)
                cmd_target._builder_data["declared_sources"] = [
                    src if isinstance(src, TargetClass) else next(normalized_iter)
                    for src in source_list
                ]

        if lazy_target or lazy_source:
            # Called by CommandNodeFactory.resolve; see make_nodes.
            cmd_target._builder_data["make_nodes"] = make_nodes
        else:
            make_nodes()

        if write_if_different:
            import sys

            python = sys.executable.replace("\\", "/")
            stable = f"{python} -m pcons.tools.stable_output"
            cmd_target.pre_build(f"{stable} --pre $out")
            cmd_target.post_build(f"{stable} --post $out")

        # A program the command runs never appears in the command's own
        # depfile, so it is an implicit dependency whatever the edge records.
        for dep in command_deps:
            cmd_target.depends(dep, on_change=True)

        if tool is not None:
            cmd_target._builder_data["tool"] = tool
            if isinstance(tool, TargetClass):
                cmd_target.depends(tool, on_change=True)

        # Apply extra implicit dependencies
        if depends is not None:
            cmd_target.depends(*_as_list(depends))

        return cmd_target

    def PyBuilder(
        self,
        *,
        python: str | None = None,
        restat: bool = False,
        write_if_different: bool = False,
        cwd: str | Path | None = None,
        launcher: Sequence[str] | None = None,
        env_vars: Mapping[str, str] | None = None,
        worker: Any = None,
        depends: Target | str | Path | Sequence[Target | str | Path] | None = None,
        emitter: Callable[..., Any] | None = None,
        discovers: bool = False,
    ) -> Callable[[Callable[..., object]], pybuilder.PyBuilder]:
        """Turn a Python function of this build script into a builder.

        The decorated name is a builder, the way ``env.Program`` is, and
        calling it makes an edge::

            @env.PyBuilder()
            def report(targets, sources, title):
                from pathlib import Path

                Path(targets[0]).write_text(title + Path(sources[0]).read_text())

            counts = report(target="counts.txt", source=["a.txt"], title="Counts")
            more = report(target="more.txt", source=["b.txt"], title="More")

            project.Default(counts, more)

        The function does not run now. Its source goes once to a generated
        module under the environment's build directory, each call's arguments
        go to a pickle beside it, and each edge runs the module at build time
        with *targets* and *sources* as the build tool spells them. Both files
        are written when pcons resolves the build, never by the call.

        The arguments here say how the function runs, which is a property of
        the body and the same for every edge. The call says what to build.
        No option appears at both, except ``depends=``: the decoration's is a
        dependency of every edge the builder makes, the call's of that edge
        alone. Two edges that must otherwise run differently are two
        decorations, which is honest about being two ways of running.

        Only the function's own source travels, so the body may use nothing
        from around it: no name the build script imported or defined, no
        variable of an enclosing function. Import what it needs inside the
        body and take everything else as a keyword of the call. Anything else
        is refused at configure time rather than at build time, and so is a
        keyword the function's signature cannot take.

        ``target``, ``source``, ``name`` and ``depends`` are refused as
        parameter names: the call spends them on the edge.

        ``depfile`` and ``deps_style`` are absent because ``discovers=True``
        does their job: the function reports what it read and pcons writes
        the depfile, so nobody writes depfile syntax.

        Args:
            python: The interpreter that runs the function, defaulting to the
                    one running pcons. A string, never a detected tool: the
                    day PyBuilder has to *find* an interpreter or ask its
                    version, that is tool knowledge and this moves to a
                    python tool. One whose file name does not contain
                    "python" makes ``worker=`` a no-op, since that is how a
                    worker recognises a command it can run in itself; the
                    command then runs directly, correctly but cold.
            restat: See :meth:`Command`.
            write_if_different: See :meth:`Command`.
            cwd: See :meth:`Command`.
            launcher: See :meth:`Command`.
            env_vars: See :meth:`Command`.
            worker: See :meth:`Command`. A :class:`pcons.workers.PythonWorker`
                    keeps an interpreter warm, which is most of the cost of a
                    small function.
            depends: Dependency of every edge the builder makes, on top of
                    whatever a call's own ``depends=`` adds. Equivalent to
                    calling ``.depends()`` on the builder this returns. See
                    :meth:`Command`.
            emitter: Names each edge's files, for a builder whose outputs
                    follow from its arguments: ``emitter(targets, sources,
                    env, **kwargs)`` returns the pair ``(targets, sources)``,
                    having added to what the call passed or replaced it. A
                    call may then leave out ``target=``. It gets the same
                    keywords the function does, and runs in the build script's
                    process when pcons resolves the edge, so it's an ordinary
                    callable: it may use anything around it, unlike the
                    function, and read what the edge's dependencies build.
            discovers: The function reads files its call never named, a
                    manifest's entries or a template's includes, and reports
                    them: it may return ``{"inputs": [...]}``, and the build
                    tool reruns the edge when one of them changes. A
                    relative path is read from the build directory, where
                    the function runs. Returning None means it read nothing
                    more; any key but ``"inputs"`` is an error. The edge may
                    have one target, which the depfile is named after, and
                    ``cwd=`` is refused with it. Discovered *outputs* are a
                    different thing: see ``project.when_generated()``.

        Returns:
            A decorator that returns the builder the script calls.
        """
        from pcons.tools.pybuilder import py_builder

        return py_builder(
            self,
            python=python,
            restat=restat,
            write_if_different=write_if_different,
            cwd=cwd,
            launcher=launcher,
            env_vars=env_vars,
            worker=worker,
            depends=depends,
            emitter=emitter,
            discovers=discovers,
        )

    def __str__(self) -> str:
        """User-friendly string representation for debugging."""
        name = object.__getattribute__(self, "_name")
        lines = [f"Environment: {name or '(unnamed)'}"]

        defined_at = object.__getattribute__(self, "defined_at")
        if defined_at:
            lines.append(f"  Defined at: {defined_at}")

        toolchain = object.__getattribute__(self, "_toolchain")
        if toolchain:
            lines.append(f"  Toolchain: {toolchain.name}")

        vars_dict = self._get_vars()
        if "variant" in vars_dict:
            lines.append(f"  Variant: {vars_dict['variant']}")
        if "build_dir" in vars_dict:
            lines.append(f"  Build dir: {vars_dict['build_dir']}")

        # Show key tool settings
        tools = self._get_tools()
        for tool_name in ["cc", "cxx", "link"]:
            if tool_name in tools:
                tool = tools[tool_name]
                cmd = tool.get("cmd", "?")
                flags = tool.get("flags", [])
                if cmd or flags:
                    flags_preview = flags[:3] if isinstance(flags, list) else []
                    suffix = "..." if isinstance(flags, list) and len(flags) > 3 else ""
                    lines.append(
                        f"  {tool_name}: cmd={cmd}, flags={flags_preview}{suffix}"
                    )

        return "\n".join(lines)

    def __repr__(self) -> str:
        tools = self._get_tools()
        vars_dict = self._get_vars()
        return (
            f"Environment(tools=[{', '.join(tools.keys())}], "
            f"vars=[{', '.join(vars_dict.keys())}])"
        )
