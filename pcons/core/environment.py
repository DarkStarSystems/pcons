# SPDX-License-Identifier: MIT
"""Environment with namespaced tool configuration.

An Environment holds configuration for a build, including tool-specific
namespaces (env.cc, env.cxx, etc.) and cross-tool variables.
"""

from __future__ import annotations

import logging
import re
from collections import UserList
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pcons.core.debug import trace, trace_value
from pcons.core.subst import Namespace, subst, to_shell_command
from pcons.core.toolconfig import ToolConfig
from pcons.core.vars import _record_variant
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core._environment_stubs import _EnvironmentStubs
    from pcons.core._preset_names import KnownFeaturePreset
    from pcons.core._toolchain_names import KnownToolchain
    from pcons.core.explain import Explanation
    from pcons.core.node import FileNode, Node
    from pcons.core.preset import Preset, ToolContribution
    from pcons.core.target import Target
    from pcons.tools.toolchain import Toolchain
else:
    # At runtime, Environment inherits from `object`; tool namespaces and
    # cross-tool variables are dispatched through __getattr__/__setattr__
    # as before. The mixin's only purpose is to declare typed names for
    # static analysis.
    _EnvironmentStubs = object

logger = logging.getLogger(__name__)


#: ``$SOURCE`` or ``${SOURCE}``, but not ``$SOURCES`` or ``${SOURCES[0]}``.
_SINGULAR_SOURCE = re.compile(r"\$SOURCE(?![S\w])|\$\{SOURCE\}")


def _warn_if_source_reads_as_singular(
    command: str | list[str], sources: int, at: SourceLocation
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

    Environments can be cloned for variant builds:
        debug = env.clone()
        debug.cc.flags += ['-g']

    Attributes:
        build_dir: Directory for build outputs.
        defined_at: Source location where this environment was created.
    """

    __slots__ = (
        "_tools",
        "_vars",
        "_project",
        "_toolchain",
        "_additional_toolchains",
        "_created_nodes",
        "_applied_presets",
        "_applied_imperative",
        "_use_origins",
        "_fanout_seen",
        "_name",
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
        }
        from pcons.core.project import Project

        self._project = Project.current()

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
        # Active only inside a set_*/apply_* fan-out (see _dedup_fanout)
        self._fanout_seen: set[Any] | None = None
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
            # After the baseline, so explain() attributes these by name.
            for preset in toolchain.setup_presets(self):
                self.apply(preset)

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

        raise AttributeError(
            f"Environment has no tool or variable '{name}'. "
            f"Tools: {', '.join(tools.keys()) or '(none)'}. "
            f"Vars: {', '.join(vars_dict.keys()) or '(none)'}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Set a cross-tool variable or replace a tool config."""
        if name.startswith("_") or name == "defined_at":
            object.__setattr__(self, name, value)
        elif isinstance(value, ToolConfig):
            tools = self._get_tools()
            tools[name] = value
            value._env = self
        else:
            vars_dict = self._get_vars()
            vars_dict[name] = value

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

    def add_toolchain(self, toolchain: Toolchain | KnownToolchain | str) -> None:
        """Add an additional toolchain to this environment.

        Additional toolchains provide extra source handlers and tools.
        The primary toolchain (from constructor) has precedence for
        output naming conventions.

        Args:
            toolchain: Toolchain to add. A string is looked up in the
                toolchain registry, like the Environment constructor.

        Example:
            env = project.Environment(toolchain="c")
            env.add_toolchain("cuda")  # Adds CUDA support
        """
        if isinstance(toolchain, str):
            from pcons.tools.toolchain import toolchain_registry

            toolchain = toolchain_registry.resolve(toolchain)
        additional: list[Toolchain] = object.__getattribute__(
            self, "_additional_toolchains"
        )
        additional.append(toolchain)
        before = set(self._get_tools().keys())
        toolchain.setup(self)
        new_tools = [n for n in self._get_tools() if n not in before]
        self._record_toolchain_baseline(toolchain, new_tools)
        for preset in toolchain.setup_presets(self):
            self.apply(preset)

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
        """Return the environment name, if set."""
        return object.__getattribute__(self, "_name")

    @name.setter
    def name(self, value: str | None) -> None:
        """Set the environment name."""
        object.__setattr__(self, "_name", value)

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

        # Add tool namespaces
        for name, config in tools.items():
            data[name] = config.as_namespace()

        return Namespace(data)

    def clone(self) -> Environment:
        """Create a deep copy of this environment.

        Tool configurations are cloned so modifications don't affect
        the original.

        Returns:
            A new Environment with copied configuration.
        """
        tools = self._get_tools()
        vars_dict = self._get_vars()

        new_env = Environment(defined_at=get_caller_location())

        # Copy cross-tool variables (deep copy lists/dicts)
        new_vars = new_env._get_vars()
        for key, value in vars_dict.items():
            if isinstance(value, list):
                new_vars[key] = list(value)
            elif isinstance(value, dict):
                new_vars[key] = dict(value)
            else:
                new_vars[key] = value

        # Clone tool configurations
        new_tools = new_env._get_tools()
        for name, config in tools.items():
            cloned = config.clone()
            cloned._env = new_env
            new_tools[name] = cloned

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
        new_env._use_origins = dict(self._use_origins)

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

        # Don't copy name - cloned env should get a new name if needed
        # (otherwise two envs could generate the same ninja rule names)
        new_env._name = None

        # Don't copy created_nodes - new environment starts fresh

        return new_env

    @contextmanager
    def override(self, **kwargs: Any) -> Iterator[Environment]:
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
        any group member already applied. A preset applies fully or
        raises (docs/presets.md, "Preset application").
        """
        if self._fanout_seen is not None:
            # Identical presets resolved by several toolchains apply once
            # (shared tools like cc would double flags otherwise).
            key = (preset.name, preset.category, preset.contributions)
            if key in self._fanout_seen:
                return
            self._fanout_seen.add(key)

        # Validate before any mutation, including the un-apply below.
        self._validate_preset(preset)

        if preset.exclusive_group is not None:
            for i, applied in enumerate(self._applied_presets):
                if applied.exclusive_group == preset.exclusive_group:
                    self._unapply_contributions(applied)
                    del self._applied_presets[i]
                    break

        for contribution in preset.contributions:
            self._apply_contribution(contribution)

        self._applied_presets.append(preset)

        if preset.category == "variant":
            self.variant = preset.name
        # Only the set_target_arch knob writes target_arch; on cross
        # presets, arch is metadata.
        if preset.category == "arch" and preset.arch is not None:
            self.target_arch = preset.arch

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

    @contextmanager
    def _dedup_fanout(self) -> Iterator[None]:
        """Scope a per-toolchain fan-out so identical presets apply once."""
        self._fanout_seen = set()
        try:
            yield
        finally:
            self._fanout_seen = None

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

    def set_variant(self, name: str, **kwargs: Any) -> None:
        """Set the build variant; each toolchain translates the name to flags.

        Args:
            name: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options passed to apply_variant().

        Example:
            env.set_variant("debug")
            env.set_variant("release", extra_flags=["-march=native"])
        """
        trace("env", "Setting variant: %s", name)
        # Recorded whether or not a toolchain realizes it, because this is the
        # only point at which a variant name is observable: `get_variant` takes
        # a string and returns one. The CLI persists the names for completion.
        _record_variant(name)
        if self.toolchains:
            with self._dedup_fanout():
                for toolchain in self.toolchains:
                    toolchain.apply_variant(self, name, **kwargs)
        else:
            # No toolchains - just set the variant name
            self.variant = name

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
        if self.toolchains:
            with self._dedup_fanout():
                realized = [
                    toolchain.apply_target_arch(self, arch, **kwargs)
                    for toolchain in self.toolchains
                ]
            if not any(realized):
                names = ", ".join(t.name for t in self.toolchains)
                raise ValueError(
                    f"No configured toolchain ({names}) realizes target "
                    f"arch '{arch}'. Retargeting the CPU on this platform "
                    f"may need a cross toolchain or cross preset instead "
                    f"(see docs/presets.md)."
                )
        else:
            self.target_arch = arch

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

    def apply_preset(self, name: KnownFeaturePreset | str) -> None:
        """Apply a named feature preset to this environment.

        Resolution is **toolchain-first, then registry**: each toolchain's
        built-in ``FEATURE_PRESETS`` is tried first, then the contributed-preset
        registry (see :func:`pcons.register_preset`). Built-ins use bare names
        (``warnings``, ``werror``, ``sanitize``, ``lto``, ``hardened``);
        contributed presets are namespaced (``scope/name``). ``explain()``
        attributes each flag to the preset that added it.

        Args:
            name: Preset name (``"warnings"`` or ``"scope/name"``).

        Example:
            env.apply_preset("warnings")
            env.apply_preset("mycorp/strict")
        """
        from pcons.core.preset import (
            apply_imperative_preset,
            is_registered_preset,
            resolve_registered_feature,
        )

        if not self.toolchains:
            logger.warning("No toolchains configured; cannot apply preset '%s'", name)
            return

        applied = False
        with self._dedup_fanout():
            for toolchain in self.toolchains:
                preset = toolchain.make_feature_preset(name)  # built-in
                if preset is None:
                    preset = resolve_registered_feature(name, toolchain)  # registry
                if preset is not None:
                    self.apply(preset)
                    applied = True
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

    def has_preset(self, name: KnownFeaturePreset | str) -> bool:
        """Whether ``apply_preset(name)`` would land contributions here.

        True if any configured toolchain realizes *name* (built-in or
        contributed declarative), or *name* is a registered imperative
        preset. False for unknown names and for optional features this
        system can't provide (e.g. ``openmp`` when no OpenMP runtime is
        available) — checking never raises.

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
            resolve_registered_feature,
        )

        for toolchain in self.toolchains:
            if toolchain.make_feature_preset(name) is not None:
                return True
            if resolve_registered_feature(name, toolchain) is not None:
                return True
        return is_imperative_preset(name)

    def apply_cross_preset(self, preset: Any) -> None:
        """Apply a cross-compilation preset to this environment.

        Cross-compilation presets configure sysroot, target triple,
        architecture flags, and SDK paths for building on a different
        platform.

        Args:
            preset: A CrossPreset dataclass instance.

        Example:
            from pcons.toolchains.presets import android, ios

            env.apply_cross_preset(android(ndk="~/android-ndk"))
            env.apply_cross_preset(ios(arch="arm64"))
        """
        if self.toolchains:
            with self._dedup_fanout():
                for toolchain in self.toolchains:
                    toolchain.apply_cross_preset(self, preset)
        else:
            logger.warning(
                "No toolchains configured; cannot apply cross-preset '%s'",
                preset.name if hasattr(preset, "name") else preset,
            )

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

        # Use project.node() for deduplication when available
        matches = list(Path(".").glob(pattern))
        if self._project is not None:
            return [self._project.node(p) for p in matches]
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
        - include_dirs → cxx.includes (and cc.includes if present)
        - defines → cxx.defines (and cc.defines if present)
        - compile_flags → cxx.flags
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
        else:
            from pcons.packages.imported import requirements_from_package

            reqs = requirements_from_package(package)

        if system:
            # Clone first: system= describes this use, not the package, which
            # may be used unsystem'd elsewhere.
            reqs = reqs.clone()
            reqs.make_includes_system()

        apply_requirements_to_env(self, reqs, origin=getattr(package, "name", None))

    def _resolve_cwd(self, cwd: str | Path | None) -> Path | None:
        """Anchor a ``cwd=`` argument, which is relative to the project root.

        Absolute at this point, so generators need no notion of where a
        working directory was spelled from.
        """
        if cwd is None:
            return None
        path = Path(cwd)
        if path.is_absolute():
            return path
        root = getattr(self._project, "root_dir", None) if self._project else None
        return (root or Path.cwd()) / path

    def Command(
        self,
        *,
        target: str | Path | list[str | Path],
        source: Target | str | Path | Sequence[Target | str | Path] | None = None,
        command: str | list[str] = "",
        name: str | None = None,
        depends: str | Path | Sequence[str | Path] | None = None,
        restat: bool = False,
        write_if_different: bool = False,
        cwd: str | Path | None = None,
        launcher: Sequence[str] | None = None,
        worker: Any = None,
    ) -> Target:
        """Run an arbitrary shell command to build targets from sources.

        This is a general-purpose builder for running shell commands that
        don't fit into the standard compile/link model. It supports variable
        substitution for common patterns.

        **BREAKING CHANGE (v0.2.0):** This method now returns a `Target` object
        instead of `list[FileNode]`, and uses keyword-only arguments. To access
        output nodes, use `target.output_nodes`.

        Args:
            target: Output file(s) that the command produces.
            source: Input file(s) that the command depends on. Can be Targets
                   (whose output files become sources), paths, or None.
            command: The shell command to run. Supports variable substitution:
                    - $SOURCE / $SOURCES: All source files (space-separated);
                      the two spellings mean the same thing
                    - $TARGET / $TARGETS: All target files (space-separated)
                    - ${SOURCES[n]}: Indexed source access (0-based)
                    - ${TARGETS[n]}: Indexed target access (0-based)
                    - ${SOURCES[n:m]}: A range of sources, either end optional
                    - $SRCDIR: Project source tree root directory. Use this
                      to reference source-tree files that aren't listed as
                      sources (e.g., config files, scripts). Example:
                      "$SRCDIR/scripts/generate.py $SOURCE $TARGET"
                    - $$: A literal dollar sign, delivered to the command
                      verbatim (the shell does not expand it). Environment
                      variables belong in the build script, via os.environ.
                    Any of these may be part of a larger argument, e.g.
                    "./${SOURCES[0]}" to run a program this build produced (a
                    POSIX shell would otherwise look a bare name up on $PATH)
                    or "--out=$TARGET". Attached to a form that expands to
                    several paths, the text repeats on each of them.
                    Any other $variable is expanded from this environment.

                    **The command runs in the build directory**, which is the
                    one place in pcons where paths are not relative to the
                    project root (``sources=`` and ``target=`` are). So a path
                    written relative — "tools/gen.pl" — is looked for under
                    the build directory and won't be found. Spell it
                    "$SRCDIR/tools/gen.pl", pass an absolute path (pcons
                    rewrites those to stay relocatable), or move the whole
                    command with ``cwd=``.

                    Do not quote a token yourself: pcons keeps the command as
                    tokens and quotes each for the shell it writes for, so
                    hand-quoting arrives at the program with the quotes still
                    attached, and a token starting with a quote raises. A
                    token that must contain a space goes in the list form,
                    which isn't split on whitespace; one whose quotes really
                    are meant goes in ``Verbatim(...)``.
            name: Optional target name for `ninja <name>`. Derived from first
                  target filename if not specified.
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
            worker: A :class:`pcons.workers.Worker` to run this command in,
                   for an action that costs more to start than to run.
                   Renders to a launcher, so the generated build file still
                   builds standalone: with no worker listening, the command
                   runs directly. See :mod:`pcons.workers`.

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
            # list. Sources keep the order given, so the tool is source 0.
            atlas = env.Command(
                target="atlas.bin",
                source=[packer, *sprites],
                command="./${SOURCES[0]} --out=$TARGET ${SOURCES[1:]}"
            )

            # Can be passed to Install() since it's a Target
            project.Install("dist/", [generated])
        """
        from pcons.core.builder import GenericCommandBuilder
        from pcons.core.node import FileNode
        from pcons.core.target import Target as TargetClass

        # Normalize target to list
        if isinstance(target, (str, Path)):
            targets = [Path(target)]
        else:
            targets = [Path(t) for t in target]

        # Derive name from first target if not specified
        if name is None:
            name = targets[0].stem

        # Normalize source to list, separating Targets from immediate sources.
        # A Target's outputs don't exist until the resolve phase, so it can't
        # become a node here — but its *position* has to survive, or $SOURCE
        # and ${SOURCES[n]} refer to different files than the script wrote.
        immediate_sources: list[str | Path | Node] = []
        target_sources: list[TargetClass] = []
        source_list: list[Any] = []

        if source is not None:
            source_list = (
                [source]
                if isinstance(source, (str, Path, TargetClass))
                else list(source)
            )
            for src in source_list:
                if isinstance(src, TargetClass):
                    target_sources.append(src)
                else:
                    immediate_sources.append(src)

        _warn_if_source_reads_as_singular(
            command, len(source_list), get_caller_location()
        )

        # Create the builder
        # A worker is a launcher with a lifecycle; the edge only ever sees
        # the tokens that route it through one.
        if worker is not None:
            launcher = [*(launcher or []), *worker.launcher()]

        builder = GenericCommandBuilder(
            command,
            restat=restat or write_if_different,
            cwd=self._resolve_cwd(cwd),
            launcher=launcher,
        )

        # Build the targets with immediate sources
        normalized = builder._normalize_sources(immediate_sources)
        nodes = builder._build(
            self,
            targets,
            normalized,
            defined_at=get_caller_location(),
        )

        # Create Target object
        cmd_target = TargetClass(
            name,
            target_type="command",
            defined_at=get_caller_location(),
        )
        cmd_target._env = self
        cmd_target._builder_name = "Command"

        # Register nodes with the environment and add to target
        for node in nodes:
            if isinstance(node, FileNode):
                self.register_node(node)
                cmd_target.output_nodes.append(node)

        if write_if_different:
            import sys

            python = sys.executable.replace("\\", "/")
            stable = f"{python} -m pcons.tools.stable_output"
            cmd_target.pre_build(f"{stable} --pre $out")
            cmd_target.post_build(f"{stable} --post $out")

        # Handle Target sources - store for deferred resolution
        if target_sources:
            cmd_target._pending_sources = list(target_sources)
            # The declared sequence, with each Target still a Target: the
            # factory substitutes its outputs in place once they exist, so
            # $SOURCES keeps the order the script wrote instead of listing
            # every Target last.
            normalized_iter = iter(normalized)
            cmd_target._builder_data["declared_sources"] = [
                src if isinstance(src, TargetClass) else next(normalized_iter)
                for src in source_list
            ]
            # Add as dependencies to ensure correct build order
            for src_target in target_sources:
                if src_target not in cmd_target.dependencies:
                    cmd_target.add_dependency(src_target)

        # Apply extra implicit dependencies
        if depends is not None:
            if isinstance(depends, (str, Path)):
                cmd_target.depends(depends)
            else:
                cmd_target.depends(*depends)

        # Register target with project if available
        if self._project is not None:
            # Handle duplicate target names by appending a suffix
            base_name = name
            counter = 1
            while name in self._project._targets:
                name = f"{base_name}_{counter}"
                counter += 1
            if name != base_name:
                cmd_target.name = name

        return cmd_target

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
