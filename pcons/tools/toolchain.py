# SPDX-License-Identifier: MIT
"""Toolchain protocol and base implementation.

A Toolchain is a coordinated set of Tools that work together
(e.g., GCC toolchain includes gcc, g++, ar, ld with compatible flags).
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pcons.core.subst import TargetPath

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.target import Target
    from pcons.tools.tool import BaseTool, Tool


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
    """

    tool_name: str
    language: str
    object_suffix: str
    depfile: TargetPath | None = None
    deps_style: str | None = None
    command_var: str = "objcmd"


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

    def register(
        self,
        toolchain_class: type[BaseToolchain],
        *,
        aliases: list[str],
        check_command: str,
        tool_classes: list[type[BaseTool]],
        category: str = "general",
    ) -> None:
        """Register a toolchain for auto-discovery.

        Args:
            toolchain_class: The toolchain class to register.
            aliases: Names this toolchain responds to (e.g., ["llvm", "clang"]).
            check_command: Command to check for availability (e.g., "clang").
            tool_classes: Tool classes to instantiate when using this toolchain.
            category: Category for grouping (e.g., "c", "python", "rust").
        """
        entry = ToolchainEntry(
            toolchain_class=toolchain_class,
            aliases=aliases,
            check_command=check_command,
            tool_classes=tool_classes,
            category=category,
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
        tried: list[str] = []
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
            tried.append(entry.aliases[0] if entry.aliases else "unknown")
            if shutil.which(entry.check_command) is not None:
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
    ) -> None:
        self.toolchain_class = toolchain_class
        self.aliases = aliases
        self.check_command = check_command
        self.tool_classes = tool_classes
        self.category = category

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

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset to the environment.

        Cross-compilation presets configure sysroot, target triple,
        architecture flags, and SDK paths.

        Args:
            env: Environment to configure.
            preset: A CrossPreset dataclass instance.
        """
        ...

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for source file suffix, or None if not handled."""
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

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        """Return additional compile flags needed for the target type."""
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


class BaseToolchain(ABC):
    """Abstract base class for toolchains.

    Provides common functionality for toolchains. Subclasses must
    provide the list of tools and configure logic.
    """

    # Default language priorities (higher = stronger)
    DEFAULT_LANGUAGE_PRIORITY: dict[str, int] = {
        "c": 1,
        "cxx": 2,
        "objc": 2,
        "objcxx": 3,
        "fortran": 3,
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

    def apply_variant(self, env: Environment, variant: str, **kwargs: Any) -> None:
        """Apply a build variant to the environment.

        Default implementation does nothing. Subclasses override this
        to implement toolchain-specific variant handling.

        Args:
            env: Environment to configure.
            variant: Variant name (e.g., "debug", "release").
            **kwargs: Toolchain-specific options.
        """
        # Store variant name on environment
        env.variant = variant

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        """Apply target architecture flags to the environment.

        Default implementation just stores the arch name. Subclasses override
        this to implement toolchain-specific architecture handling, calling
        super().apply_target_arch() to store the name.

        Args:
            env: Environment to configure.
            arch: Architecture name (e.g., "arm64", "x86_64", "x64").
            **kwargs: Toolchain-specific options.
        """
        # Store target architecture name on environment
        env.target_arch = arch

    def apply_preset(self, env: Environment, name: str) -> None:  # noqa: B027
        """Apply a named flag preset to the environment.

        Default implementation does nothing. Subclasses override this
        to implement toolchain-specific preset handling.

        Args:
            env: Environment to configure.
            name: Preset name (e.g., "warnings", "sanitize").
        """

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        """Apply a cross-compilation preset to the environment.

        Default implementation applies generic CrossPreset fields.
        Subclasses override this for toolchain-specific handling.

        Args:
            env: Environment to configure.
            preset: A CrossPreset dataclass instance.
        """
        # Apply architecture if specified
        if hasattr(preset, "arch") and preset.arch:
            self.apply_target_arch(env, preset.arch)

        # Apply sysroot to cc, cxx, link
        if hasattr(preset, "sysroot") and preset.sysroot:
            sysroot_flag = f"--sysroot={preset.sysroot}"
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.append(sysroot_flag)
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.append(sysroot_flag)

        # Apply extra compile flags
        if hasattr(preset, "extra_compile_flags") and preset.extra_compile_flags:
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.extend(preset.extra_compile_flags)

        # Apply extra link flags
        if hasattr(preset, "extra_link_flags") and preset.extra_link_flags:
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.extend(preset.extra_link_flags)

        # Override CC/CXX commands from env_vars
        if hasattr(preset, "env_vars") and preset.env_vars:
            for var_name, value in preset.env_vars.items():
                tool_name = var_name.lower()
                if tool_name in ("cc", "cxx") and env.has_tool(tool_name):
                    getattr(env, tool_name).cmd = value

    def get_linker_for_languages(self, languages: set[str]) -> str:
        """Determine which tool should link based on languages used.

        Args:
            languages: Set of language names (e.g., {'c', 'cxx'}).

        Returns:
            Tool name to use for linking.
        """
        if not languages:
            return "link"

        # Find the highest priority language
        priority = self.language_priority
        max_priority = -1
        max_lang = "c"

        for lang in languages:
            p = priority.get(lang, 0)
            if p > max_priority:
                max_priority = p
                max_lang = lang

        # Map language to linker tool
        # (subclasses may override this mapping)
        return self._linker_for_language(max_lang)

    def _linker_for_language(self, language: str) -> str:
        """Get the linker tool name for a language.

        Override in subclasses if the mapping is different.
        """
        # Default: use the language's compiler as linker
        # (e.g., 'cxx' means use g++ to link)
        if language == "c":
            return "cc"
        elif language in ("cxx", "objcxx"):
            return "cxx"
        elif language == "fortran":
            return "fortran"
        elif language == "cuda":
            return "cuda"
        else:
            return "link"

    # =========================================================================
    # Source Handler Methods - Override in subclasses for tool-agnosticism
    # =========================================================================

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

    def get_static_library_name(self, name: str) -> str:
        """Return filename for a static library.

        Override in subclasses for platform-specific naming.
        Default is Unix-style "lib{name}.a".

        Args:
            name: Library base name.

        Returns:
            Full library filename (e.g., "libfoo.a").
        """
        return f"lib{name}.a"

    def get_shared_library_name(self, name: str) -> str:
        """Return filename for a shared library.

        Override in subclasses for platform-specific naming.
        Default is Unix-style "lib{name}.so".

        Args:
            name: Library base name.

        Returns:
            Full library filename (e.g., "libfoo.so", "libfoo.dylib").
        """
        return f"lib{name}.so"

    def get_program_name(self, name: str) -> str:
        """Return filename for a program.

        Override in subclasses for platform-specific naming.
        Default has no suffix (Unix-style).

        Args:
            name: Program base name.

        Returns:
            Full program filename (e.g., "myapp", "myapp.exe").
        """
        return name

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

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token.

        Override in subclasses to provide toolchain-specific flags.
        Default implementation returns an empty frozenset.

        Returns:
            A frozenset of flag strings that take separate arguments.
        """
        return frozenset()

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
        from pcons.core.requirements import compute_effective_requirements
        from pcons.toolchains.build_context import CompileLinkContext

        # Compute effective requirements
        effective = compute_effective_requirements(target, env, for_compilation)

        # Create and return context
        return CompileLinkContext.from_effective_requirements(effective)

    def __repr__(self) -> str:
        tools = ", ".join(self._tools.keys())
        return f"{self.__class__.__name__}({self.name!r}, tools=[{tools}])"
