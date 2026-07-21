# SPDX-License-Identifier: MIT
"""Toolchain protocol and base implementation.

A Toolchain is a coordinated set of Tools that work together
(e.g., GCC toolchain includes gcc, g++, ar, ld with compatible flags).
"""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from pcons.core.preset import Preset, ToolContribution
from pcons.core.subst import TargetPath

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.subst import PathToken
    from pcons.core.target import Target
    from pcons.toolchains.build_context import CompileLinkContext
    from pcons.tools.tool import BaseTool, Tool


# =============================================================================
# C++ setting methods (exposed on the cxx tool namespace via Toolchain.tool_setting).
# Realization stays per-toolchain (make_cxx_standard_preset).
# =============================================================================

_CXX_STANDARDS: frozenset[int] = frozenset({11, 14, 17, 20, 23, 26})


def _parse_cxx_standard(standard: int | str) -> int:
    """Normalize ``"c++20"`` / ``"20"`` / ``20`` to the integer ``20``."""
    text = str(standard).strip().lower().removeprefix("c++").removeprefix("gnu++")
    try:
        n = int(text)
    except ValueError:
        raise ValueError(
            f"Invalid C++ standard {standard!r}; use e.g. 'c++20' or 20"
        ) from None
    if n not in _CXX_STANDARDS:
        allowed = ", ".join(str(s) for s in sorted(_CXX_STANDARDS))
        raise ValueError(f"Unsupported C++ standard 'c++{n}'; supported: {allowed}")
    return n


def _cxx_set_standard(env: Environment, standard: int | str) -> None:
    """``env.cxx.set_standard(...)`` — select the C++ language standard."""
    n = _parse_cxx_standard(standard)
    for toolchain in env.toolchains:
        preset = toolchain.make_cxx_standard_preset(n)
        if preset is not None:
            env.apply(preset)


# =============================================================================
# Toolchain Context - provides variables for build statements
# =============================================================================


@runtime_checkable
class ToolchainContext(Protocol):
    """Toolchain-specific build context.

    Provides values that fill placeholders in command templates, keeping
    domain-specific fields (effective includes/defines, ...) out of the
    core BuildInfo. CompileLinkContext is the C/C++ implementation.
    """

    def get_env_overrides(self) -> dict[str, object]:
        """Return values to set on env.<tool>.* before command expansion.

        Set on the tool namespace so template expressions like
        ${prefix(cc.iprefix, cc.includes)} expand with the effective
        requirements.
        """
        ...


# =============================================================================
# Source Handler - describes how a toolchain handles a source file type
# =============================================================================


CXX_MODULE_INTERFACE_SUFFIXES: frozenset[str] = frozenset(
    {".cppm", ".ixx", ".cxxm", ".c++m"}
)
"""File suffixes recognized as C++20 module interface units.

Includes Microsoft's `.ixx`, Clang's `.cppm`/`.cxxm`/`.c++m`. Both LLVM and
MSVC toolchains accept any of these — the compiler is forced into C++ module
mode via toolchain-specific flags (`/TP /interface` for MSVC, `-x c++-module`
for clang) regardless of which extension the user picks.
"""


@dataclass
class SourceHandler:
    """Describes how a toolchain handles a source file type.

    Attributes:
        tool_name: Name of the tool to use (e.g., "cc", "cxx", "latex").
        language: Language of the source (e.g., "c", "cxx", "latex").
        object_suffix: Suffix for compiled objects (e.g., ".o", ".obj", ".aux").
        depfile: Dependency file specification:
            - TargetPath(suffix=".d"): Depfile path derived from target output
            - None: No dependency tracking
        deps_style: Dependency file style (e.g., "gcc", "msvc") or None.
        command_var: Name of the command variable (e.g., "objcmd", "rccmd").
        group_sources: If True, all of a target's sources matching this
                       handler compile in ONE invocation producing one
                       object (whole-module compilation, e.g. Swift). The
                       command template sees all sources; the toolchain
                       can augment the grouped node via setup_group_node().
    """

    tool_name: str
    language: str
    object_suffix: str
    depfile: TargetPath | None = None
    deps_style: str | None = None
    command_var: str = "objcmd"
    group_sources: bool = False


@dataclass
class AuxiliaryInputHandler:
    """Describes how a toolchain handles auxiliary input files.

    These files are not compiled but passed directly to a downstream tool
    with specific flags. Examples include .def files passed to the linker,
    .bib files passed to bibtex, or asset manifests passed to packers.

    Attributes:
        suffix: File suffix this handles (e.g., ".def")
        flag_template: Flag template for the downstream tool. Use $file for
                      the file path. Example: "/DEF:$file"
        tool: Which downstream tool receives this file (e.g., "link", "bibtex")
        extra_flags: Additional flags to add (once, not per-file). Useful for
                    flags like "/manifest:embed" that should accompany the handler.
    """

    suffix: str
    flag_template: str
    tool: str = "link"
    extra_flags: list[str] | None = None


# =============================================================================
# Toolchain Registry
# =============================================================================


@dataclass
class _FinderEntry:
    """A registered auto-detection finder (see ToolchainRegistry.register_finder)."""

    finder: Callable[[], BaseToolchain | None]
    description: str = ""


class ToolchainRegistry:
    """Registry for toolchains that support auto-discovery.

    Toolchains register themselves with metadata needed for automatic
    detection and instantiation. This allows find_toolchain() to work
    without hardcoding toolchain-specific information.

    Example:
        # In gcc.py, after class definition:
        toolchain_registry.register(
            GccToolchain,
            aliases=["gcc", "gnu"],
            check_command="gcc",
            tool_classes=[GccCCompiler, GccCxxCompiler, GccArchiver, GccLinker],
            category="c",
        )
    """

    def __init__(self) -> None:
        self._toolchains: dict[str, ToolchainEntry] = {}
        self._finders: dict[str, _FinderEntry] = {}

    def register_finder(
        self,
        names: Sequence[str],
        finder: Callable[[], BaseToolchain | None],
        *,
        description: str = "",
    ) -> None:
        """Register an auto-detection finder under one or more names.

        Finders are curated entry points like ``find_c_toolchain`` that pick
        the best available toolchain for a language or platform. ``resolve()``
        checks finder names before toolchain aliases, so a finder can shadow
        an alias of the same name with richer detection and error reporting.

        Args:
            names: Names the finder responds to (e.g., ["c", "c++", "cpp"]).
            finder: Callable returning a configured toolchain, or None if
                nothing suitable is available (resolve() turns that into an
                error). May also raise with a descriptive message.
            description: Short human-readable description for listings.
        """
        entry = _FinderEntry(finder=finder, description=description)
        for name in names:
            self._finders[name.lower()] = entry

    def known_names(self) -> dict[str, str]:
        """All resolvable names (finders and aliases) with descriptions.

        Used for the generated ``KnownToolchain`` Literal and for error
        messages. Finder names come first so their descriptions win when a
        name is both a finder and an alias.
        """
        names: dict[str, str] = {}
        for alias, tc_entry in self._toolchains.items():
            names[alias] = tc_entry.description
        for name, f_entry in self._finders.items():
            names[name] = f_entry.description
        return dict(sorted(names.items()))

    def resolve(self, spec: str | Sequence[str]) -> BaseToolchain:
        """Resolve a toolchain name (or preference list) to a toolchain.

        A single string is either a finder name ("c", "fortran", ...) for
        auto-detection, or a specific toolchain alias ("gcc", "msvc", ...)
        which must be available. A sequence is a preference list: the first
        name that resolves to an available toolchain wins.

        Args:
            spec: Toolchain name, or a preference-ordered list of names.

        Returns:
            A configured toolchain ready for use.

        Raises:
            ValueError: If a name is unknown (lists all known names).
            RuntimeError: If the named toolchain(s) are not available.
        """
        if isinstance(spec, str):
            return self._resolve_one(spec)

        errors: list[str] = []
        for name in spec:
            try:
                return self._resolve_one(name)
            except (RuntimeError, ValueError) as e:
                errors.append(f"{name}: {e}")
        raise RuntimeError(
            "No toolchain available from preference list "
            f"{list(spec)}:\n  " + "\n  ".join(errors)
        )

    def _resolve_one(self, name: str) -> BaseToolchain:
        key = name.lower()

        finder = self._finders.get(key)
        if finder is not None:
            toolchain = finder.finder()
            if toolchain is None:
                raise RuntimeError(f"No '{name}' toolchain found on this system")
            return toolchain

        entry = self._toolchains.get(key)
        if entry is not None:
            if entry.is_available is not None:
                available = entry.is_available()
            else:
                available = shutil.which(entry.check_command) is not None
            if not available:
                raise RuntimeError(
                    f"Toolchain '{name}' is not available "
                    f"('{entry.check_command}' not found in PATH)"
                )
            return entry.create_toolchain()

        known = ", ".join(sorted({*self._finders, *self._toolchains}))
        raise ValueError(f"Unknown toolchain '{name}'. Known names: {known}")

    def register(
        self,
        toolchain_class: type[BaseToolchain],
        *,
        aliases: list[str],
        check_command: str,
        tool_classes: list[type[BaseTool]],
        category: str = "general",
        platforms: list[str] | None = None,
        description: str = "",
        finder: str = "",
        is_available: Callable[[], bool] | None = None,
    ) -> None:
        """Register a toolchain for auto-discovery.

        Args:
            toolchain_class: The toolchain class to register.
            aliases: Names this toolchain responds to (e.g., ["llvm", "clang"]).
            check_command: Command to check for availability (e.g., "clang").
            tool_classes: Tool classes to instantiate when using this toolchain.
            category: Category for grouping (e.g., "c", "python", "rust").
            platforms: Platform names where this toolchain is available
                       (e.g., ["linux", "darwin", "win32"]). Uses sys.platform values.
            description: Short human-readable description of the toolchain.
            finder: Name of the finder function (e.g., "find_c_toolchain()").
            is_available: Optional custom availability check. If provided, called
                instead of ``shutil.which(check_command)``. Should return True
                if the toolchain can be used.
        """
        entry = ToolchainEntry(
            toolchain_class=toolchain_class,
            aliases=aliases,
            check_command=check_command,
            tool_classes=tool_classes,
            category=category,
            platforms=platforms or [],
            description=description,
            finder=finder,
            is_available=is_available,
        )
        # Register under all aliases
        for alias in aliases:
            self._toolchains[alias.lower()] = entry

    def get(self, name: str) -> ToolchainEntry | None:
        """Get toolchain entry by name."""
        return self._toolchains.get(name.lower())

    def find_available(
        self,
        category: str,
        prefer: list[str] | None = None,
    ) -> BaseToolchain | None:
        """Find the first available toolchain in a category.

        Args:
            category: Category to search (e.g., "c").
            prefer: Ordered list of toolchain names to try first.

        Returns:
            A configured toolchain, or None if none available.
        """
        # Collect unique entries in preference order
        entries_to_try: list[ToolchainEntry] = []
        seen_classes: set[type] = set()

        # First, try preferred toolchains in order
        if prefer:
            for name in prefer:
                entry = self.get(name)
                if entry and entry.category == category:
                    if entry.toolchain_class not in seen_classes:
                        entries_to_try.append(entry)
                        seen_classes.add(entry.toolchain_class)

        # Then try any remaining toolchains in the category
        for entry in self._toolchains.values():
            if entry.category == category:
                if entry.toolchain_class not in seen_classes:
                    entries_to_try.append(entry)
                    seen_classes.add(entry.toolchain_class)

        # Try each entry
        for entry in entries_to_try:
            if entry.is_available is not None:
                if entry.is_available():
                    return entry.create_toolchain()
            elif shutil.which(entry.check_command) is not None:
                return entry.create_toolchain()

        return None

    def get_tried_names(
        self,
        category: str,
        prefer: list[str] | None = None,
    ) -> list[str]:
        """Get the list of toolchain names that would be tried."""
        tried: list[str] = []
        seen_classes: set[type] = set()

        if prefer:
            for name in prefer:
                entry = self.get(name)
                if entry and entry.category == category:
                    if entry.toolchain_class not in seen_classes:
                        tried.append(entry.aliases[0] if entry.aliases else name)
                        seen_classes.add(entry.toolchain_class)

        for entry in self._toolchains.values():
            if entry.category == category:
                if entry.toolchain_class not in seen_classes:
                    tried.append(entry.aliases[0] if entry.aliases else "unknown")
                    seen_classes.add(entry.toolchain_class)

        return tried


class ToolchainEntry:
    """Metadata for a registered toolchain."""

    def __init__(
        self,
        toolchain_class: type[BaseToolchain],
        aliases: list[str],
        check_command: str,
        tool_classes: list[type[BaseTool]],
        category: str,
        platforms: list[str] | None = None,
        description: str = "",
        finder: str = "",
        is_available: Callable[[], bool] | None = None,
    ) -> None:
        self.toolchain_class = toolchain_class
        self.aliases = aliases
        self.check_command = check_command
        self.tool_classes = tool_classes
        self.category = category
        self.platforms = platforms or []
        self.description = description
        self.finder = finder
        self.is_available = is_available

    def create_toolchain(self) -> BaseToolchain:
        """Create and configure a toolchain instance."""
        toolchain = self.toolchain_class()
        # Set up tools without requiring full configure()
        toolchain._tools = {}
        for tool_class in self.tool_classes:
            tool = tool_class()
            toolchain._tools[tool.name] = tool
        toolchain._configured = True
        return toolchain


# Global registry instance
toolchain_registry = ToolchainRegistry()


@runtime_checkable
class Toolchain(Protocol):
    """Protocol for toolchains.

    A Toolchain represents a coordinated set of tools that work together.
    Switching toolchains switches all related tools atomically.
    """

    @property
    def name(self) -> str:
        """Toolchain name (e.g., 'gcc', 'llvm', 'msvc')."""
        ...

    @property
    def tools(self) -> dict[str, Tool]:
        """Tools in this toolchain, keyed by tool name."""
        ...

    @property
    def language_priority(self) -> dict[str, int]:
        """Language priority for linker selection.

        Higher values = stronger language. When linking objects from
        multiple languages, use the linker for the highest-priority
        language.
        """
        ...

    def configure(self, config: object) -> bool:
        """Configure all tools in this toolchain.

        Args:
            config: Configure context.

        Returns:
            True if the toolchain is available and configured.
        """
        ...

    def setup(self, env: Environment) -> None:
        """Add all tools to an environment.

        Args:
            env: Environment to set up.
        """
        ...

    def setup_presets(self, env: Environment) -> list[Preset]:
        """Presets to apply right after setup (attributed by explain())."""
        ...

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply a build variant (e.g. "debug", "release"); each toolchain
        defines its own semantics."""
        ...

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> bool:
        """Apply target-arch flags (e.g. -arch on macOS, /MACHINE: on MSVC).

        Returns True if this toolchain realized the arch; the environment
        raises when no configured toolchain did (docs/presets.md).
        """
        ...

    def apply_preset(self, env: Environment, name: str) -> None:
        """Apply a named flag preset (warnings, sanitize, lto, ...);
        each toolchain defines its own flags."""
        ...

    def make_feature_preset(self, name: str) -> Preset | None:
        """Build a built-in feature Preset by name, or None if not known."""
        ...

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset (sysroot, triple, SDK paths)."""
        ...

    def make_cxx_standard_preset(self, standard: int) -> Preset | None:
        """Build a Preset selecting the C++ *standard* (e.g. 20), or None
        if not C++."""
        ...

    def tool_setting(self, tool: str, name: str) -> Callable[..., None] | None:
        """Return a setting method for ``env.<tool>.<name>``, or None."""
        ...

    def compile_link_context_class(self) -> type[CompileLinkContext]:
        """Return the CompileLinkContext subclass for compile/link commands."""
        ...

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled."""
        ...

    def setup_group_node(self, node: Any, target: Target, env: Environment) -> None:
        """Augment a grouped (whole-module) compile node; usually a no-op."""
        ...

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for auxiliary input files, or None if not handled."""
        ...

    def get_object_suffix(self) -> str:
        """Return the object file suffix for this toolchain."""
        ...

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library."""
        ...

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library."""
        ...

    def get_program_name(self, name: str) -> str:
        """Return filename for a program."""
        ...

    def get_output_prefix(self, target_type: str) -> str:
        """Return the default output filename prefix for a target type.

        E.g., "lib" for shared/static libraries on Unix, "" on Windows.
        """
        ...

    def get_output_suffix(self, target_type: str) -> str:
        """Return the default output filename suffix for a target type.

        E.g., ".so" / ".dylib" / ".dll" for shared libraries.
        """
        ...

    def get_install_dir(self, target_type: str) -> str:
        """Return the conventional install subdirectory for a target type.

        E.g., "bin" for programs, "lib" for static libraries, and "bin" or
        "lib" for shared libraries depending on the target platform.
        """
        ...

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type."""
        ...

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[str | PathToken],
    ) -> list[str]:
        """Return additional target-specific link flags (e.g. install_name,
        SONAME). *existing_flags* lets the toolchain skip defaults the user
        already overrode.
        """
        ...

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags (like -framework, -arch) whose argument is a separate
        token, for flag deduplication."""
        ...

    def get_archiver_tool_name(self) -> str:
        """Return the archiver tool name ("ar" for GCC, "lib" for MSVC)."""
        ...

    def get_runtime_libs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return runtime library names (no -l prefix) needed for
        mixed-language builds."""
        ...

    def get_runtime_libdirs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return library search dirs for mixed-language runtime libs."""
        ...

    def create_build_context(
        self,
        target: Target,
        env: Environment,
        for_compilation: bool = True,
    ) -> ToolchainContext | None:
        """Create a toolchain-specific build context for a target, or None
        if this toolchain doesn't use the context mechanism."""
        ...

    def after_resolve(
        self,
        project: Any,
        source_obj_by_language: dict[str, list[Any]],
    ) -> None:
        """Optional post-resolution hook, called before command expansion.

        Args:
            project: The resolved project.
            source_obj_by_language: All (source_path, obj_node) pairs grouped
                by language (e.g., ``{"fortran": [...], "cxx": [...]}``.
        """
        ...


class BaseToolchain(ABC):
    """Abstract base class for toolchains.

    Provides common functionality for toolchains. Subclasses must
    provide the list of tools and configure logic.
    """

    # Tool namespaces this toolchain installs into env._tools at setup.
    # Concrete subclasses override this; the generator reads it to build
    # the Environment typing stub.
    TOOL_NAMES: ClassVar[tuple[str, ...]] = ()

    # Default language priorities (higher = stronger).
    # Fortran is NOT listed here so that C/C++ toolchains don't claim
    # priority over Fortran objects. GfortranToolchain overrides this
    # to add "fortran": 3 when it is the primary toolchain.
    DEFAULT_LANGUAGE_PRIORITY: dict[str, int] = {
        "c": 1,
        "cxx": 2,
        "objc": 2,
        "objcxx": 3,
        "cuda": 4,
    }

    def __init__(self, name: str = "") -> None:
        """Initialize a toolchain.

        Args:
            name: Toolchain name. Subclasses should always provide this.
        """
        self._name = name
        self._tools: dict[str, Tool] = {}
        self._configured = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def tools(self) -> dict[str, Tool]:
        return self._tools

    @property
    def language_priority(self) -> dict[str, int]:
        """Override in subclasses if needed."""
        return self.DEFAULT_LANGUAGE_PRIORITY

    def configure(self, config: object) -> bool:
        """Configure all tools.

        Subclasses should override _configure_tools() to set up
        the _tools dict.
        """
        if self._configured:
            return True

        result = self._configure_tools(config)
        self._configured = result
        return result

    @abstractmethod
    def _configure_tools(self, config: object) -> bool:
        """Configure the toolchain's tools.

        Subclasses implement this to detect and configure tools.

        Args:
            config: Configure context.

        Returns:
            True if configuration succeeded.
        """
        ...

    def setup(self, env: Environment) -> None:
        """Set up all tools in the environment."""
        for tool in self._tools.values():
            tool.setup(env)

    # =========================================================================
    # Presets (variants, features, cross targets)
    #
    # Variants, feature presets, and cross-compilation targets all reduce to a
    # declarative Preset (a bundle of ToolContributions). Toolchains build them
    # via the make_*_preset() / *_contributions() hooks below; Environment.apply()
    # applies the opaque tokens and records the preset so it can be explained.
    # The apply_*() methods are thin wrappers kept for call-site readability.
    # =========================================================================

    def setup_presets(self, env: Environment) -> list[Preset]:
        """Presets this toolchain applies right after setup (default: none).

        Runs after the environment records the toolchain's baseline, so
        anything returned here is attributed by explain() to the preset
        (e.g. ``cc.cmd <- wasi-sdk``) instead of blending into the
        toolchain defaults. This is how a toolchain points tools at a
        detected SDK declaratively rather than mutating env attributes in
        setup() (docs/presets.md).
        """
        return []

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply a build variant (e.g. "debug", "release") to the environment."""
        env.apply(self.make_variant_preset(variant, **kwargs))

    def make_variant_preset(self, variant: str, **kwargs: Any) -> Preset:
        """Build a variant Preset. Base contributes no flags."""
        return Preset(
            name=variant,
            category="variant",
            exclusive_group="build_variant",
            contributions=tuple(self._variant_contributions(variant, **kwargs)),
        )

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        """Tool contributions for a variant. Base knows no variants."""
        return []

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> bool:
        """Apply target architecture flags to the environment.

        Returns True if this toolchain realized the arch (produced
        contributions). An empty realization returns False and applies
        nothing; the environment raises if *no* configured toolchain
        realized the arch (docs/presets.md, "Preset application"), so a
        custom toolchain that simply doesn't handle an arch is fail-fast
        by default. A toolchain for which an arch is legitimately a no-op
        declares that by overriding this method (see WasmToolchain).
        """
        contribs = tuple(self._arch_contributions(arch))
        if not contribs:
            return False
        env.apply(Preset(name=arch, category="arch", arch=arch, contributions=contribs))
        return True

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Tool contributions for a target arch. Base realizes none."""
        return []

    def apply_preset(self, env: Environment, name: str) -> None:
        """Apply a named feature preset (e.g. "warnings", "sanitize")."""
        preset = self.make_feature_preset(name)
        if preset is None:
            logger.warning("Unknown preset '%s' for toolchain '%s'", name, self.name)
            return
        env.apply(preset)

    def make_cxx_standard_preset(self, standard: int) -> Preset | None:
        """Build a C++-standard Preset, or None if this toolchain has no C++.

        The flag spelling is toolchain-specific (``-std=c++20`` vs
        ``/std:c++20``); subclasses provide it via ``_cxx_standard_flag``.
        """
        flag = self._cxx_standard_flag(standard)
        if flag is None:
            return None
        return Preset(
            name=f"c++{standard}",
            category="language",
            contributions=(ToolContribution("cxx", flags=(flag,)),),
        )

    def _cxx_standard_flag(self, standard: int) -> str | None:
        """Compiler flag selecting C++ *standard*, or None if not a C++ toolchain."""
        return None

    def tool_setting(self, tool: str, name: str) -> Callable[..., None] | None:
        """Return a setting method ``(env, *args) -> None`` for ``env.<tool>.<name>``.

        Settings are domain methods exposed on a tool namespace (e.g.
        ``env.cxx.set_standard``). The realization stays per-toolchain
        (``make_cxx_standard_preset`` etc.); a toolchain that doesn't realize a
        setting simply no-ops. Returns None for unknown settings. See
        docs/presets.md.
        """
        if tool == "cxx" and name == "set_standard":
            return _cxx_set_standard
        return None

    # Named feature presets for this toolchain, keyed by preset name. Each value
    # maps "compile_flags"/"link_flags" to flag lists, realized on the
    # toolchain's compile tools (see _feature_preset_tools). Subclasses populate
    # this; the built-in flags live here, near the toolchain. See docs/presets.md.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {}

    def _feature_preset_tools(self) -> tuple[str, ...]:
        """Compile tools that feature-preset compile_flags apply to.

        Defaults to the C/C++ compilers; Fortran-style toolchains override
        (e.g. ``("fc",)``).
        """
        return ("cc", "cxx")

    def make_feature_preset(self, name: str) -> Preset | None:
        """Build a feature Preset by name, or None if this toolchain lacks it.

        Realizes the named entry's compile flags on the toolchain's compile
        tools and link flags on ``link``. See docs/presets.md.
        """
        spec = self.FEATURE_PRESETS.get(name)
        if spec is None:
            return None
        contribs: list[ToolContribution] = []
        compile_flags = spec.get("compile_flags", [])
        if compile_flags:
            for tool in self._feature_preset_tools():
                contribs.append(ToolContribution(tool, flags=tuple(compile_flags)))
        link_flags = spec.get("link_flags", [])
        if link_flags:
            contribs.append(ToolContribution("link", flags=tuple(link_flags)))
        return Preset(name=name, category="feature", contributions=tuple(contribs))

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation target preset (a CrossPreset)."""
        env.apply(self.make_target_preset(preset))

    def make_target_preset(self, cross: Any) -> Preset:
        """Convert a CrossPreset descriptor into a Preset."""
        return Preset(
            name=getattr(cross, "name", "target"),
            category="target",
            arch=getattr(cross, "arch", None),
            contributions=tuple(self._target_contributions(cross)),
        )

    def _target_contributions(self, cross: Any) -> list[ToolContribution]:
        """Tool contributions for a cross target (extra flags, cmd overrides).

        Toolchains extend this (UnixToolchain adds triple/sysroot; WasmToolchain
        narrows it to extra flags only). The preset's `arch` is metadata, not a
        flag source: the triple already encodes the CPU, and arch-flag
        vocabulary belongs to the set_target_arch knob (see docs/presets.md).
        """
        contribs: list[ToolContribution] = []
        contribs.extend(self._extra_flag_contributions(cross))
        contribs.extend(self._cmd_contributions(cross))
        return contribs

    @staticmethod
    def _extra_flag_contributions(cross: Any) -> list[ToolContribution]:
        """cc/cxx extra_compile_flags and link extra_link_flags from a CrossPreset."""
        contribs: list[ToolContribution] = []
        compile_flags = getattr(cross, "extra_compile_flags", ())
        if compile_flags:
            contribs.append(ToolContribution("cc", flags=tuple(compile_flags)))
            contribs.append(ToolContribution("cxx", flags=tuple(compile_flags)))
        link_flags = getattr(cross, "extra_link_flags", ())
        if link_flags:
            contribs.append(ToolContribution("link", flags=tuple(link_flags)))
        return contribs

    @staticmethod
    def _cmd_contributions(cross: Any) -> list[ToolContribution]:
        """Per-tool command overrides from a CrossPreset.

        Reads tool_cmds (keyed by pcons tool name) merged with the
        deprecated env_vars aliases via CrossPreset.resolved_tool_cmds();
        any tool the preset names can be repointed (cc, cxx, link, ar, ...).
        A duck-typed descriptor without that helper supplies plain
        tool_cmds only (env_vars translation lives in CrossPreset).
        """
        resolve = getattr(cross, "resolved_tool_cmds", None)
        if resolve is not None:
            cmds = resolve()
        else:
            cmds = dict(getattr(cross, "tool_cmds", None) or {})
        return [ToolContribution(tool, cmd=cmd) for tool, cmd in sorted(cmds.items())]

    @staticmethod
    def _sysroot_contributions(cross: Any) -> list[ToolContribution]:
        """--sysroot flag on cc/cxx/link from a CrossPreset."""
        sysroot = getattr(cross, "sysroot", None)
        if not sysroot:
            return []
        flag = f"--sysroot={sysroot}"
        return [ToolContribution(t, flags=(flag,)) for t in ("cc", "cxx", "link")]

    def get_runtime_libs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return runtime library names (no -l prefix) needed for
        mixed-language builds. Base: none."""
        return []

    def get_runtime_libdirs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return search dirs for get_runtime_libs() libraries installed in
        non-standard locations (e.g. Homebrew gfortran). Base: none."""
        return []

    # =========================================================================
    # Source Handler Methods - Override in subclasses for tool-agnosticism
    # =========================================================================

    def after_resolve(  # noqa: B027
        self,
        project: Any,
        source_obj_by_language: dict[str, list[Any]],
    ) -> None:
        """Optional post-resolution hook, called before command expansion.

        Override to inspect or modify the resolved build graph (e.g.
        Fortran dyndep generation). Base does nothing.

        Args:
            project: The resolved project.
            source_obj_by_language: All compiled (source_path, obj_node)
                pairs, grouped by language name.
        """

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for a source suffix (".c", ".tex"), or None.

        Override to define what sources this toolchain handles; the
        resolver queries this instead of hardcoding file types.
        """
        return None

    def setup_group_node(  # noqa: B027 — optional hook, intentionally a no-op
        self, node: Any, target: Target, env: Environment
    ) -> None:
        """Augment a grouped (whole-module) compile node. No-op by default.

        Called after the single compile node for a ``group_sources=True``
        handler is created. Override to add per-node template variables
        (``node._build_info["vars"]``), extra outputs, or implicit deps —
        e.g. Swift's ``-module-name`` and ``.swiftmodule`` emission.
        """

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for an auxiliary input suffix (".def"), or None.

        Auxiliary inputs are passed directly to a downstream tool with
        specific flags rather than compiled to object files.
        """
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix (".o", ".obj"). Default ".o"."""
        return ".o"

    def get_archiver_tool_name(self) -> str:
        """Return the archiver tool name ("ar" for GCC, "lib" for MSVC)."""
        return "ar"

    def get_output_prefix(self, target_type: str) -> str:
        """Return the default output filename prefix for a target type.

        Override in subclasses for platform/toolchain-specific naming.
        Default is "lib" for libraries, "" for programs.
        """
        from pcons.configure.platform import get_platform

        plat = get_platform()
        if target_type == "static_library":
            return plat.static_lib_prefix
        elif target_type == "shared_library":
            return plat.shared_lib_prefix
        return ""

    def get_output_suffix(self, target_type: str) -> str:
        """Return the default output filename suffix for a target type.

        Override in subclasses for platform/toolchain-specific naming.
        """
        from pcons.configure.platform import get_platform

        plat = get_platform()
        if target_type == "static_library":
            return plat.static_lib_suffix
        elif target_type == "shared_library":
            return plat.shared_lib_suffix
        return plat.exe_suffix

    def get_install_dir(self, target_type: str) -> str:
        """Return the conventional install subdirectory for a target type.

        The convention follows the platform this toolchain targets:

        - programs go in ``bin``
        - static libraries (and archives) go in ``lib``
        - shared libraries go in ``bin`` when the toolchain produces Windows
          DLLs (a ``.dll`` must sit next to the executable that loads it), and
          in ``lib`` otherwise (ELF ``.so`` / Mach-O ``.dylib``).

        Override in subclasses for non-standard layouts (e.g. ``lib64``).
        """
        if target_type == "program":
            return "bin"
        if target_type == "shared_library":
            if self.get_output_suffix("shared_library") == ".dll":
                return "bin"
            return "lib"
        # static_library and anything else (archives, data) default to lib.
        return "lib"

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library."""
        return f"{self.get_output_prefix('static_library')}{name}{self.get_output_suffix('static_library')}"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library."""
        return f"{self.get_output_prefix('shared_library')}{name}{self.get_output_suffix('shared_library')}"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program."""
        return f"{self.get_output_prefix('program')}{name}{self.get_output_suffix('program')}"

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags for a target type (e.g. -fPIC for
        shared libraries on Linux). Base: none."""
        return []

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[str | PathToken],
    ) -> list[str]:
        """Return additional target-specific link flags (e.g. install_name,
        SONAME). *existing_flags* lets subclasses skip defaults the user
        already overrode. Base: none.
        """
        return []

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags whose argument is a separate token. Base: none."""
        return frozenset()

    def compile_link_context_class(self) -> type[CompileLinkContext]:
        """Return the CompileLinkContext subclass used for compile/link commands.

        The context class controls how flags and library names are formatted
        for this toolchain's tools. The GNU-style default passes library
        names through unchanged (``-lfoo`` is built by the command template);
        MSVC-compatible toolchains override this to format ``foo.lib``.
        """
        from pcons.toolchains.build_context import CompileLinkContext

        return CompileLinkContext

    def create_build_context(
        self,
        target: Target,
        env: Environment,
        for_compilation: bool = True,
    ) -> ToolchainContext | None:
        """Create a toolchain-specific build context for a target.

        Args:
            target: The target being built.
            env: The build environment.
            for_compilation: If True, create context for compilation;
                             if False, for linking.
        """
        from pcons.toolchains.build_context import CompileLinkContext
        from pcons.tools.requirements import compute_effective_requirements

        effective = compute_effective_requirements(target, env, for_compilation)

        mode = "compile" if for_compilation else "link"
        return CompileLinkContext.from_effective_requirements(
            effective,
            mode=mode,
        )

    def __repr__(self) -> str:
        tools = ", ".join(self._tools.keys())
        return f"{self.__class__.__name__}({self.name!r}, tools=[{tools}])"
