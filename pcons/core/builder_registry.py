# SPDX-License-Identifier: MIT
"""Builder registration system for extensible builders.

This module provides the infrastructure for registering builders (both built-in
and user-defined) so they can be accessed as methods on Project instances.

All builders, including built-in ones like Program, Install, and Tarfile,
register through this system. This ensures user-defined builders are on equal
footing with built-ins.

Example:
    # Register a builder using the decorator
    @builder("InstallSymlink", target_type=TargetType.INTERFACE)
    class InstallSymlinkBuilder:
        @staticmethod
        def create_target(project, dest, source, **kwargs):
            ...

    # The builder is now available on any Project instance
    project.InstallSymlink("dist/latest", app)

    # Expansion packs can register multiple builders
    def register(project=None):
        BuilderRegistry.register("CompileShaders", ...)
        BuilderRegistry.register("PackageAssets", ...)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target, TargetType


@runtime_checkable
class NodeFactory(Protocol):
    """Protocol for builder-specific node factories.

    Each builder type has a factory that knows how to resolve targets
    of that type into concrete build nodes.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory with a project reference."""
        ...

    def resolve(self, target: Target, env: Environment | None) -> None:
        """Resolve the target, creating output nodes.

        This is called during the first resolution phase to create
        object nodes and output nodes based on the target's configuration.
        """
        ...

    def resolve_pending(self, target: Target) -> None:
        """Resolve pending sources for the target.

        This is called during the second resolution phase to handle
        targets with pending sources (like Install targets that reference
        other targets).
        """
        ...


@dataclass
class BuilderRegistration:
    """Metadata for a registered builder.

    Attributes:
        name: The builder name (e.g., "Program", "Install").
        create_target: Function to create a target for this builder.
        target_type: The TargetType for targets created by this builder.
        factory_class: Optional NodeFactory class for resolution.
        requires_env: Whether the builder requires an Environment argument.
        description: Human-readable description of the builder.
    """

    name: str
    create_target: Callable[..., Target]
    target_type: TargetType
    factory_class: type | None = None
    requires_env: bool = False
    description: str = ""
    # Additional options for the builder
    options: dict[str, Any] = field(default_factory=dict)


class BuilderRegistry:
    """Global registry for builders.

    This class maintains a registry of all available builders. Both built-in
    builders and user-defined builders register here, ensuring they're all
    on equal footing.

    The registry is a class with class methods so it can be used globally
    without needing to pass around an instance.
    """

    _builders: dict[str, BuilderRegistration] = {}

    @classmethod
    def register(
        cls,
        name: str,
        *,
        create_target: Callable[..., Target],
        target_type: TargetType,
        factory_class: type | None = None,
        requires_env: bool = False,
        description: str = "",
        **options: Any,
    ) -> None:
        """Register a builder.

        Args:
            name: The builder name. This becomes the method name on Project.
            create_target: Function to create a Target for this builder.
                Should have signature: (project, *args, **kwargs) -> Target
            target_type: The TargetType for targets created by this builder.
            factory_class: Optional NodeFactory class for resolution.
            requires_env: Whether the builder requires an Environment argument.
            description: Human-readable description of the builder.
            **options: Additional builder-specific options.
        """
        cls._builders[name] = BuilderRegistration(
            name=name,
            create_target=create_target,
            target_type=target_type,
            factory_class=factory_class,
            requires_env=requires_env,
            description=description,
            options=options,
        )

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a builder.

        Args:
            name: The builder name to unregister.
        """
        cls._builders.pop(name, None)

    @classmethod
    def get(cls, name: str) -> BuilderRegistration | None:
        """Get a builder registration by name.

        Args:
            name: The builder name.

        Returns:
            The BuilderRegistration, or None if not found.
        """
        return cls._builders.get(name)

    @classmethod
    def names(cls) -> list[str]:
        """Get all registered builder names.

        Returns:
            List of builder names.
        """
        return list(cls._builders.keys())

    @classmethod
    def all(cls) -> dict[str, BuilderRegistration]:
        """Get all builder registrations.

        Returns:
            Dictionary mapping names to registrations.
        """
        return dict(cls._builders)

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations. Primarily for testing."""
        cls._builders.clear()


def builder(
    name: str,
    *,
    target_type: TargetType,
    factory_class: type | None = None,
    requires_env: bool = False,
    description: str = "",
    **options: Any,
) -> Callable[[type], type]:
    """Decorator to register a builder class.

    The decorated class must have a `create_target` static method or class method
    that creates and returns a Target.

    Example:
        @builder("InstallSymlink", target_type=TargetType.INTERFACE)
        class InstallSymlinkBuilder:
            @staticmethod
            def create_target(project, dest, source, *, name=None):
                target = Target(...)
                target._builder_name = "InstallSymlink"
                target._builder_data = {"dest": dest, "source": source}
                project.add_target(target)
                return target

    Args:
        name: The builder name.
        target_type: The TargetType for targets created by this builder.
        factory_class: Optional NodeFactory class for resolution.
        requires_env: Whether the builder requires an Environment argument.
        description: Human-readable description of the builder.
        **options: Additional builder-specific options.

    Returns:
        Decorator function.
    """

    def decorator(cls: type) -> type:
        # Get the create_target method from the class
        create_target = getattr(cls, "create_target", None)
        if create_target is None:
            raise ValueError(
                f"Builder class {cls.__name__} must have a 'create_target' method"
            )

        # Use the class docstring as description if not provided
        desc = description or cls.__doc__ or ""

        BuilderRegistry.register(
            name,
            create_target=create_target,
            target_type=target_type,
            factory_class=factory_class,
            requires_env=requires_env,
            description=desc,
            **options,
        )

        # Store the registration name on the class for reference
        cls._builder_name = name  # type: ignore[attr-defined]

        return cls

    return decorator
