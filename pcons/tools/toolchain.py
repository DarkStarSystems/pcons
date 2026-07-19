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

    Provides values that fill placeholders in command templates.
    The toolchain controls what variables exist and how they're formatted.

    This protocol allows toolchains to define domain-specific build contexts
    without polluting the core BuildInfo with C/C++ specific fields like
    effective_includes, effective_defines, etc.

    The context provides get_env_overrides() which returns values to be set
    on the environment's tool namespace before command template expansion.
    This allows the resolver to expand commands with effective requirements
    at generation time, rather than writing per-build Ninja variables.

    Example implementations:
    - CompileLinkContext: For C/C++ compilation and linking
    - DocumentContext: For document generation (hypothetical)
    - AssetBundleContext: For asset bundling (hypothetical)
    """

    def get_env_overrides(self) -> dict[str, object]:
        """Return values to set on env.<tool>.* before command expansion.

        These values are set on the environment's tool namespace so that
        template expressions like ${prefix(cc.iprefix, cc.includes)} are
        expanded during subst() with the effective requirements.

        Returns:
            Dictionary mapping variable names to values.
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

    This allows toolchains to define what source file types they can process
    without hardcoding this information in the resolver.

    Attributes:
        tool_name: Name of the tool to use (e.g., "cc", "cxx", "latex").
        language: Language of the source (e.g., "c", "cxx", "latex").
        object_suffix: Suffix for compiled objects (e.g., ".o", ".obj", ".aux").
        depfile: Dependency file specification:
            - TargetPath(suffix=".d"): Depfile path derived from target output
            - None: No dependency tracking
        deps_style: Dependency file style (e.g., "gcc", "msvc") or None.
        command_var: Name of the command variable (e.g., "objcmd", "rccmd").
                     Defaults to "objcmd" for backwards compatibility.
        group_sources: If True, all of a target's sources matching this
                       handler compile in ONE invocation producing one
                       object (whole-module compilation — Swift-style
                       languages where the compilation unit is the module,
                       not the file). The command template sees all sources
                       (bare SourcePath() renders them all); the toolchain
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

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply a build variant to the environment.

        Toolchains implement this to configure their tools for different
        build variants (e.g., "debug", "release"). The core knows nothing
        about what these variants mean - each toolchain defines its own
        semantics.

        Args:
            env: Environment to configure.
            variant: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options.
        """
        ...

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags to the environment.

        Toolchains implement this to configure their tools for different
        CPU architectures. The core knows nothing about what these
        architectures mean - each toolchain defines its own semantics.

        For example:
        - GCC/LLVM on macOS: adds -arch flags to compiler and linker
        - MSVC: adds /MACHINE:xxx to linker
        - Clang-CL: adds --target flag to compiler

        Args:
            env: Environment to configure.
            arch: Architecture name (e.g., "arm64", "x86_64", "x64").
            **kwargs: Toolchain-specific options.
        """
        ...

    def apply_preset(self, env: Environment, name: str) -> None:
        """Apply a named flag preset to the environment.

        Presets provide commonly-used flag combinations (warnings, sanitize,
        profile, lto, hardened). Each toolchain defines its own flags.

        Args:
            env: Environment to configure.
            name: Preset name.
        """
        ...

    def make_feature_preset(self, name: str) -> Preset | None:
        """Build a built-in feature Preset by name, or None if not known."""
        ...

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset to the environment.

        Cross-compilation presets configure sysroot, target triple,
        architecture flags, and SDK paths.

        Args:
            env: Environment to configure.
            preset: A CrossPreset dataclass instance.
        """
        ...

    def make_cxx_standard_preset(self, standard: int) -> Preset | None:
        """Build a Preset selecting the C++ *standard*, or None if not C++.

        Args:
            standard: C++ standard as an integer (e.g. 20 for C++20).
        """
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
        """Return additional link flags for a specific target.

        Called during resolution to inject target-specific link flags
        such as install_name (macOS) or SONAME (Linux) for shared
        libraries. The *existing_flags* are provided so that the
        toolchain can skip defaults when the user has already set
        an explicit override.

        Args:
            target: The target being linked.
            output_name: The output filename (e.g., ``libfoo.dylib``).
            existing_flags: Link flags already collected for this target.

        Returns:
            List of additional link flags. Default returns empty list.
        """
        ...

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        These are flags like -F, -framework, -arch where the argument
        is a separate command-line token rather than attached to the flag.
        This information is needed for proper flag deduplication.

        Returns:
            A frozenset of flag strings that take separate arguments.
        """
        ...

    def get_archiver_tool_name(self) -> str:
        """Return the name of the archiver tool for this toolchain.

        Different toolchains use different tool names:
        - GCC uses "ar"
        - MSVC uses "lib"
        """
        ...

    def get_runtime_libs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return runtime libraries needed for mixed-language builds.

        Args:
            linker_language: The language driving the link.
            object_languages: All languages present in the object files.

        Returns:
            List of library names to add (without -l prefix).
        """
        ...

    def get_runtime_libdirs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return library search directories for mixed-language runtime libs.

        Args:
            linker_language: The language driving the link.
            object_languages: All languages present in the object files.

        Returns:
            List of directory paths to add as library search dirs.
        """
        ...

    def create_build_context(
        self,
        target: Target,
        env: Environment,
        for_compilation: bool = True,
    ) -> ToolchainContext | None:
        """Create a toolchain-specific build context for a target.

        This is the factory method that creates the appropriate context
        object for this toolchain. The context provides variables that
        fill placeholders in command templates.

        Args:
            target: The target being built.
            env: The build environment.
            for_compilation: If True, create context for compilation.
                            If False, create context for linking.

        Returns:
            A ToolchainContext providing variables for the build statement,
            or None if this toolchain doesn't use the context mechanism.
        """
        ...

    def after_resolve(
        self,
        project: Any,
        source_obj_by_language: dict[str, list[Any]],
    ) -> None:
        """Optional post-resolution hook.

        Called after all targets are resolved but before command expansion.
        Override to inspect or modify the resolved build graph (e.g., Fortran
        dyndep generation). The base implementation does nothing.

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

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags to the environment."""
        env.apply(
            Preset(
                name=arch,
                category="arch",
                arch=arch,
                contributions=tuple(self._arch_contributions(arch)),
            )
        )

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Tool contributions for a target arch. Base adds none (just records)."""
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
        """Tool contributions for a cross target (arch flags, extra flags, cmd).

        Toolchains extend this (UnixToolchain adds triple/sysroot; WasmToolchain
        narrows it to extra flags only).
        """
        contribs: list[ToolContribution] = []
        if getattr(cross, "arch", None):
            contribs.extend(self._arch_contributions(cross.arch))
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
        """cc/cxx command overrides from a CrossPreset's env_vars (CC/CXX)."""
        contribs: list[ToolContribution] = []
        env_vars = getattr(cross, "env_vars", None) or {}
        for var_name, value in env_vars.items():
            tool_name = var_name.lower()
            if tool_name in ("cc", "cxx"):
                contribs.append(ToolContribution(tool_name, cmd=value))
        return contribs

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
        """Return runtime libraries needed for mixed-language builds.

        Called during link setup when the set of object languages is known.
        The base implementation returns an empty list. Toolchains that
        require runtime libraries for mixed-language builds should override
        this method.

        Args:
            linker_language: The language driving the link (e.g., "fortran").
            object_languages: All languages present in the object files.

        Returns:
            List of library names to add (without -l prefix).
        """
        return []

    def get_runtime_libdirs(
        self, linker_language: str, object_languages: set[str]
    ) -> list[str]:
        """Return library search directories for mixed-language runtime libs.

        Companion to get_runtime_libs(). Called with the same arguments to
        provide the search paths needed to find the runtime libraries.
        Required when runtime libraries are installed in non-standard
        locations (e.g., Homebrew gfortran on macOS).

        Args:
            linker_language: The language driving the link.
            object_languages: All languages present in the object files.

        Returns:
            List of directory paths to add as library search dirs.
        """
        return []

    # =========================================================================
    # Source Handler Methods - Override in subclasses for tool-agnosticism
    # =========================================================================

    def after_resolve(  # noqa: B027
        self,
        project: Any,
        source_obj_by_language: dict[str, list[Any]],
    ) -> None:
        """Optional post-resolution hook.

        Called by the resolver after all targets have been resolved but
        before command expansion. Toolchains that need to inspect or modify
        the resolved build graph (e.g., to generate dyndep files for Fortran
        module dependencies) should override this method.

        The base implementation does nothing.

        Args:
            project: The resolved project.
            source_obj_by_language: All compiled (source_path, obj_node) pairs,
                grouped by language name. For example:
                    {"fortran": [(Path("foo.f90"), FileNode(...)), ...],
                     "cxx":     [(Path("bar.cpp"), FileNode(...)), ...]}
        """

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled.

        Override in subclasses to define what sources this toolchain handles.
        This allows the resolver to be tool-agnostic - it queries the toolchain
        instead of having hardcoded knowledge about file types.

        Args:
            suffix: File suffix including dot (e.g., ".c", ".cpp", ".tex").

        Returns:
            SourceHandler describing how to compile, or None if not handled.
        """
        return None

    def setup_group_node(  # noqa: B027 — optional hook, intentionally a no-op
        self, node: Any, target: Target, env: Environment
    ) -> None:
        """Augment a grouped (whole-module) compile node. No-op by default.

        Called by CompileLinkFactory after it creates the single compile
        node for a SourceHandler with ``group_sources=True``. Toolchains
        override this to add per-node template variables
        (``node._build_info["vars"]``, expanded into the command template),
        extra outputs (``node._build_info["outputs"]``), or implicit deps —
        e.g. Swift's ``-module-name`` and ``.swiftmodule`` emission.

        Args:
            node: The grouped compile FileNode (``_build_info`` populated).
            target: The target whose sources the node compiles.
            env: The environment the node was created with.
        """

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for auxiliary input files, or None if not handled.

        Override in subclasses to define what auxiliary input files this toolchain
        handles. Auxiliary inputs are passed directly to a downstream tool with
        specific flags rather than being compiled to object files.

        Args:
            suffix: File suffix including dot (e.g., ".def").

        Returns:
            AuxiliaryInputHandler describing how to pass to downstream tool,
            or None if not an auxiliary input.
        """
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix for this toolchain.

        Override in subclasses. Defaults to ".o" for Unix-like systems.

        Returns:
            Object file suffix (e.g., ".o", ".obj").
        """
        return ".o"

    def get_archiver_tool_name(self) -> str:
        """Return the name of the archiver tool for this toolchain.

        Different toolchains use different tool names:
        - GCC uses "ar"
        - MSVC uses "lib"

        Override in subclasses. Default is "ar" for Unix-like systems.

        Returns:
            Archiver tool name (e.g., "ar", "lib").
        """
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
        """Return additional compile flags needed for the target type.

        Override in subclasses for platform/toolchain-specific flags.
        For example, GCC/LLVM on Linux need -fPIC for shared libraries.

        Args:
            target_type: The target type (e.g., "shared_library", "static_library",
                        "program", "interface", "object").

        Returns:
            List of additional compile flags needed for this target type.
            Default implementation returns an empty list.
        """
        return []

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[str | PathToken],
    ) -> list[str]:
        """Return additional link flags for a specific target.

        Override in subclasses for platform/toolchain-specific flags
        like install_name (macOS) or SONAME (Linux).

        Args:
            target: The target being linked.
            output_name: The output filename (e.g., ``libfoo.dylib``).
            existing_flags: Link flags already collected for this target.

        Returns:
            List of additional link flags. Default returns empty list.
        """
        return []

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        Override in subclasses to provide toolchain-specific flags.
        Default implementation returns an empty frozenset.

        Returns:
            A frozenset of flag strings that take separate arguments.
        """
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

        Default implementation returns None, meaning the toolchain doesn't
        use the context mechanism. Subclasses should override this to return
        an appropriate context object (e.g., CompileLinkContext for C/C++).

        Args:
            target: The target being built.
            env: The build environment.
            for_compilation: If True, create context for compilation.
                            If False, create context for linking.

        Returns:
            A ToolchainContext providing variables for the build statement,
            or None if this toolchain doesn't use the context mechanism.
        """
        # Import here to avoid circular imports
        from pcons.toolchains.build_context import CompileLinkContext
        from pcons.tools.requirements import compute_effective_requirements

        # Compute effective requirements
        effective = compute_effective_requirements(target, env, for_compilation)

        # Create and return context
        mode = "compile" if for_compilation else "link"
        return CompileLinkContext.from_effective_requirements(
            effective,
            mode=mode,
        )

    def __repr__(self) -> str:
        tools = ", ".join(self._tools.keys())
        return f"{self.__class__.__name__}({self.name!r}, tools=[{tools}])"
