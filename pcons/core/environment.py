# SPDX-License-Identifier: MIT
"""Environment with namespaced tool configuration.

An Environment holds configuration for a build, including tool-specific
namespaces (env.cc, env.cxx, etc.) and cross-tool variables.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pcons.core.debug import trace, trace_value
from pcons.core.subst import Namespace, subst, to_shell_command
from pcons.core.toolconfig import ToolConfig
from pcons.util.source_location import SourceLocation, get_caller_location

if TYPE_CHECKING:
    from pcons.core._environment_stubs import _EnvironmentStubs
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
            # Record the toolchain's base flags before any user/preset edits, so
            # explain() can attribute them (e.g. /nologo) to the toolchain.
            self._record_toolchain_baseline(toolchain, list(self._get_tools().keys()))

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
        """Create a temporary environment with overrides.

        Returns a context manager that yields a cloned environment with
        the specified overrides applied. Useful for building targets with
        temporarily modified settings.

        Args:
            **kwargs: Variables or tool settings to override.
                     For tool settings, use tool__attr notation (e.g., cc__flags)
                     since Python kwargs can't contain dots.

        Yields:
            A cloned Environment with overrides applied. The cloned environment
            is fully independent, so you can also modify it directly within
            the context block.

        Example:
            # Override cross-tool variables
            with env.override(variant="profile") as profile_env:
                project.Program("app_profile", profile_env, sources=["main.cpp"])

            # Override tool settings using double-underscore notation
            with env.override(cxx__flags=["-fno-exceptions"]) as no_except_env:
                project.Library("mylib", no_except_env, sources=["lib.cpp"])

            # The yielded env is a full clone - you can modify it directly too
            with env.override(variant="debug") as debug_env:
                debug_env.cxx.defines.append("EXTRA_DEBUG")
                debug_env.cxx.flags.extend(["-g3", "-fno-omit-frame-pointer"])
                project.Library("mylib_debug", debug_env, sources=["lib.cpp"])
        """
        temp_env = self.clone()

        for key, value in kwargs.items():
            if "__" in key:
                # Tool attribute override: cc__flags -> env.cc.flags
                tool_name, attr_name = key.split("__", 1)
                tool = temp_env.add_tool(tool_name)  # Returns existing or creates new
                setattr(tool, attr_name, value)
            else:
                # Cross-tool variable override
                setattr(temp_env, key, value)

        yield temp_env

    # Convenience methods for common patterns

    def apply(self, preset: Preset) -> None:
        """Apply a :class:`Preset`, recording it for later explanation.

        Extends each contribution's tool flag/define lists and, for ``cmd``
        contributions, replaces the tool command. The applied preset is
        appended to this environment's history (see :meth:`explain`).

        Presets sharing an ``exclusive_group`` are mutually exclusive: applying
        a second, differently-named preset in the group raises ``ValueError``.
        Clone the environment to build multiple variants.

        Application follows the contract in docs/presets.md ("Preset
        application"): a preset applies fully or raises. A ``cmd``
        contribution to a tool this environment doesn't have is an error
        (a command swap is a retargeting mechanism that must not vanish);
        flag/define contributions to a subset of missing tools are skipped
        (broadcast semantics), but a preset none of whose contributions
        land raises. A preset with no contributions at all is the
        realizer's deliberate no-op and is accepted.

        The core is tool-agnostic — toolchains build presets via
        ``make_variant_preset``/``make_feature_preset``/``make_target_preset``;
        this method just applies the opaque tokens they carry.
        """
        if self._fanout_seen is not None:
            # Inside a set_*/apply_* fan-out over toolchains: identical
            # resolved presets apply once, however many toolchains resolved
            # them (shared tools like cc/link would double flags otherwise).
            key = (preset.name, preset.category, preset.contributions)
            if key in self._fanout_seen:
                return
            self._fanout_seen.add(key)

        if preset.exclusive_group is not None:
            for applied in self._applied_presets:
                if (
                    applied.exclusive_group == preset.exclusive_group
                    and applied.name != preset.name
                ):
                    raise ValueError(
                        f"Cannot apply '{preset.name}': '{applied.name}' from "
                        f"the same group '{preset.exclusive_group}' is already "
                        f"applied. Clone the environment to build multiple "
                        f"variants."
                    )

        # Validate up front so application is atomic: raise before any
        # contribution has been applied.
        if preset.contributions:
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

        for contribution in preset.contributions:
            self._apply_contribution(contribution)

        self._applied_presets.append(preset)

        if preset.category == "variant":
            self.variant = preset.name
        # env.target_arch has a single writer: the set_target_arch knob
        # (category "arch"). Cross presets carry arch as metadata only.
        if preset.category == "arch" and preset.arch is not None:
            self.target_arch = preset.arch

    @property
    def applied_presets(self) -> tuple[Preset, ...]:
        """Presets applied to this environment, in order (for inspection)."""
        return tuple(self._applied_presets)

    def explain(self, tool: str | None = None) -> Explanation:
        """Explain where each tool flag/define came from.

        Replays the presets applied to this environment and attributes each
        flag, define, and replaced command to the preset that contributed it;
        tokens from toolchain defaults or direct edits are labelled ``(manual)``.

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

        tools = self._get_tools()
        names = [tool] if tool is not None else list(tools.keys())
        snapshot: dict[str, dict[str, Any]] = {}
        for name in names:
            if name not in tools:
                continue
            snapshot[name] = tools[name].as_dict()
        return _explain(self._applied_presets, snapshot, self._applied_imperative)

    def _record_toolchain_baseline(
        self, toolchain: Toolchain, tool_names: list[str]
    ) -> None:
        """Record a toolchain's base flags/defines as a 'toolchain' preset.

        Called right after ``toolchain.setup()`` (before any user or preset
        edits), so the captured tokens are exactly the toolchain's defaults
        (e.g. ``/nologo``, ``rcs``). explain() then attributes them to the
        toolchain instead of labelling them ``(manual)``.
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

    @contextmanager
    def _dedup_fanout(self) -> Iterator[None]:
        """Scope a set_*/apply_* fan-out so identical resolved presets apply once.

        See apply(): within this scope, presets with the same (name, category,
        contributions) are applied a single time even when several configured
        toolchains resolve them onto shared tools.
        """
        self._fanout_seen = set()
        try:
            yield
        finally:
            self._fanout_seen = None

    def _apply_contribution(self, c: ToolContribution) -> None:
        """Apply a single tool contribution (extend flags/defines, set cmd).

        Missing tools were validated by apply(); a remaining miss here is a
        flag/define broadcast to a tool this env doesn't have — skipped.
        """
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
        """Set the build variant.

        Delegates to each toolchain's apply_variant() method for all
        configured toolchains. Each toolchain is responsible for translating
        the variant name into appropriate tool-specific settings.

        The core knows nothing about what variants mean - it's just a name.
        Each toolchain defines its own semantics (e.g., GCC defines "debug"
        as -O0 -g, while a CUDA toolchain might add -G for device debugging).

        Args:
            name: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options passed to apply_variant().

        Example:
            env.set_variant("debug")
            env.set_variant("release", extra_flags=["-march=native"])
        """
        trace("env", "Setting variant: %s", name)
        if self.toolchains:
            with self._dedup_fanout():
                for toolchain in self.toolchains:
                    toolchain.apply_variant(self, name, **kwargs)
        else:
            # No toolchains - just set the variant name
            self.variant = name

    def set_target_arch(self, arch: str, **kwargs: Any) -> None:
        """Set the target CPU architecture for cross-compilation.

        Delegates to each toolchain's apply_target_arch() method for all
        configured toolchains. Each toolchain is responsible for translating
        the architecture name into appropriate tool-specific flags.

        The core knows nothing about what architectures mean - it's just a
        string. Each toolchain defines its own semantics (e.g., GCC/LLVM on
        macOS uses -arch flags, while MSVC uses /MACHINE: linker flags).

        This is orthogonal to the variant system - you can combine them:
            env.set_variant("release")
            env.set_target_arch("arm64")

        Args:
            arch: Architecture name (e.g., "arm64", "x86_64", "x64").
            **kwargs: Toolchain-specific options passed to apply_target_arch().

        Example:
            # macOS universal binary build
            env_arm64 = project.Environment(toolchain=toolchain)
            env_arm64.set_target_arch("arm64")

            env_x86_64 = project.Environment(toolchain=toolchain)
            env_x86_64.set_target_arch("x86_64")

            # Windows cross-compilation
            env.set_target_arch("arm64")  # Uses /MACHINE:ARM64 for MSVC
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
        """Wrap compile commands with a compiler cache tool.

        Prepends ccache or sccache to the cc and cxx commands. Only wraps
        compile tools, never the linker or archiver.

        Args:
            tool: "ccache", "sccache", or None for auto-detect.
                  Auto-detect tries sccache first, then ccache.
        """
        import shutil

        if tool is None:
            for candidate in ("sccache", "ccache"):
                if shutil.which(candidate):
                    tool = candidate
                    break
            if tool is None:
                logger.warning("No compiler cache found (tried sccache, ccache)")
                return

        if tool not in ("ccache", "sccache"):
            logger.warning("Unknown compiler cache tool '%s'", tool)
            return

        if not shutil.which(tool):
            logger.warning("Compiler cache '%s' not found in PATH", tool)
            return

        # Warn about ccache + MSVC incompatibility
        if tool == "ccache":
            for tool_name in ("cc", "cxx"):
                if self.has_tool(tool_name):
                    t = getattr(self, tool_name)
                    cmd = t.get("cmd", "")
                    if isinstance(cmd, str) and ("cl.exe" in cmd or cmd.endswith("cl")):
                        logger.warning(
                            "ccache does not support MSVC cl.exe; use sccache instead"
                        )
                        return

        for tool_name in ("cc", "cxx"):
            if self.has_tool(tool_name):
                t = getattr(self, tool_name)
                cmd = t.get("cmd", "")
                if isinstance(cmd, str) and cmd and not cmd.startswith(tool):
                    t.cmd = f"{tool} {cmd}"

    def apply_preset(self, name: str) -> None:
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
        # A registered preset whose resolver returned None for these toolchains is
        # a deliberate no-op (not applicable). An unknown name is an error:
        # a typo'd preset must not produce a quietly less-configured build.
        if not applied and not is_registered_preset(name):
            available = sorted(
                {
                    p
                    for toolchain in self.toolchains
                    for p in getattr(toolchain, "FEATURE_PRESETS", {})
                }
            )
            raise ValueError(
                f"Unknown preset '{name}'. Toolchain built-ins here: "
                f"{', '.join(available) or '(none)'}; contributed presets "
                f"are listed by pcons.list_presets()."
            )

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
        if not hasattr(link, "frameworks"):
            link.frameworks = []
        if not hasattr(link, "frameworkdirs"):
            link.frameworkdirs = []

        for name in names:
            if name not in link.frameworks:
                link.frameworks.append(name)

        if dirs:
            for d in dirs:
                if d not in link.frameworkdirs:
                    link.frameworkdirs.append(d)

    def use(self, package: Any) -> None:
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

        Example:
            # Find and use a package
            pkg = finder.find("fmt")
            env.use(pkg)

            # Or with ImportedTarget
            target = ImportedTarget.from_package(pkg)
            env.use(target)

            # Multiple packages
            for pkg in [fmt_pkg, spdlog_pkg]:
                env.use(pkg)
        """
        # Compile settings - apply to cxx (and cc if present)
        if hasattr(package, "include_dirs"):
            for inc_dir in package.include_dirs:
                if self.has_tool("cxx"):
                    self.cxx.includes.append(str(inc_dir))
                if self.has_tool("cc"):
                    self.cc.includes.append(str(inc_dir))

        if hasattr(package, "defines"):
            for define in package.defines:
                if self.has_tool("cxx"):
                    self.cxx.defines.append(define)
                if self.has_tool("cc"):
                    self.cc.defines.append(define)

        if hasattr(package, "compile_flags"):
            for flag in package.compile_flags:
                if self.has_tool("cxx"):
                    self.cxx.flags.append(flag)
                if self.has_tool("cc"):
                    self.cc.flags.append(flag)

        # Link settings
        if hasattr(package, "library_dirs"):
            if self.has_tool("link"):
                for lib_dir in package.library_dirs:
                    self.link.libdirs.append(str(lib_dir))

        if hasattr(package, "libraries"):
            if self.has_tool("link"):
                for lib in package.libraries:
                    self.link.libs.append(lib)

        if hasattr(package, "link_flags"):
            if self.has_tool("link"):
                for flag in package.link_flags:
                    self.link.flags.append(flag)

        # Framework settings (macOS)
        if hasattr(package, "frameworks"):
            if self.has_tool("link"):
                for fw in package.frameworks:
                    if not hasattr(self.link, "frameworks"):
                        self.link.frameworks = []
                    if fw not in self.link.frameworks:
                        self.link.frameworks.append(fw)

        if hasattr(package, "framework_dirs"):
            if self.has_tool("link"):
                for fw_dir in package.framework_dirs:
                    if not hasattr(self.link, "frameworkdirs"):
                        self.link.frameworkdirs = []
                    if str(fw_dir) not in self.link.frameworkdirs:
                        self.link.frameworkdirs.append(str(fw_dir))

    def Command(
        self,
        *,
        target: str | Path | list[str | Path],
        source: Target | str | Path | Sequence[Target | str | Path] | None = None,
        command: str | list[str] = "",
        name: str | None = None,
        depends: str | Path | Sequence[str | Path] | None = None,
        restat: bool = False,
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
                    - $SOURCE: First source file
                    - $SOURCES: All source files (space-separated)
                    - $TARGET: First target file
                    - $TARGETS: All target files (space-separated)
                    - ${SOURCES[n]}: Indexed source access (0-based)
                    - ${TARGETS[n]}: Indexed target access (0-based)
                    - $SRCDIR: Project source tree root directory. Use this
                      to reference source-tree files that aren't listed as
                      sources (e.g., config files, scripts). Example:
                      "$SRCDIR/scripts/generate.py $SOURCE $TARGET"
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

        # Normalize source to list, separating Targets from immediate sources
        immediate_sources: list[str | Path | Node] = []
        target_sources: list[TargetClass] = []

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

        # Create the builder
        builder = GenericCommandBuilder(command, restat=restat)

        # Build the targets with immediate sources
        nodes = builder._build(
            self,
            targets,
            builder._normalize_sources(immediate_sources),
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

        # Handle Target sources - store for deferred resolution
        if target_sources:
            cmd_target._pending_sources = list(target_sources)
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
