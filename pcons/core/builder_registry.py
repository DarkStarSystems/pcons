# SPDX-License-Identifier: MIT
"""Builder registration system.

All builders — built-in (Program, Install, Tarfile, ...) and user-defined —
register here to become methods on Project instances.

Example:
    @builder("InstallSymlink", target_type="interface")
    class InstallSymlinkBuilder:
        @staticmethod
        def create_target(project, dest, source, **kwargs):
            ...

    # The builder is now available on any Project instance
    project.InstallSymlink("dist/latest", app)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target


@runtime_checkable
class NodeFactory(Protocol):
    """Protocol for builder-specific node factories.

    Each builder type has a factory that resolves targets of that type
    into concrete build nodes.
    """

    def __init__(self, project: Project) -> None:
        """Initialize the factory with a project reference."""
        ...

    def resolve(self, target: Target, env: Environment | None) -> None:
        """First resolution phase: create the target's object/output nodes."""
        ...

    def resolve_pending(self, target: Target) -> None:
        """Second resolution phase: resolve sources that reference other
        targets (e.g. Install targets)."""
        ...


@dataclass
class BuilderRegistration:
    """Metadata for a registered builder; see :meth:`BuilderRegistry.register`."""

    name: str
    create_target: Callable[..., Target]
    target_type: str
    factory_class: type | None = None
    requires_env: bool = False
    description: str = ""
    platforms: list[str] = field(default_factory=list)
    # Additional options for the builder
    options: dict[str, Any] = field(default_factory=dict)


class BuilderRegistry:
    """Global registry for builders (class methods only; no instance needed)."""

    _builders: dict[str, BuilderRegistration] = {}

    @classmethod
    def register(
        cls,
        name: str,
        *,
        create_target: Callable[..., Target],
        target_type: str,
        factory_class: type | None = None,
        requires_env: bool = False,
        description: str = "",
        platforms: list[str] | None = None,
        **options: Any,
    ) -> None:
        """Register a builder.

        Args:
            name: The builder name. This becomes the method name on Project.
            create_target: Function to create a Target for this builder.
                Should have signature: (project, *args, **kwargs) -> Target
            target_type: The str for targets created by this builder.
            factory_class: Optional NodeFactory class for resolution.
            requires_env: Whether the builder requires an Environment argument.
            description: Human-readable description of the builder.
            platforms: Platform names where this builder is available
                       (e.g., ["linux", "darwin", "win32"]). None/empty means all.
            **options: Additional builder-specific options.
        """
        cls._builders[name] = BuilderRegistration(
            name=name,
            create_target=create_target,
            target_type=target_type,
            factory_class=factory_class,
            requires_env=requires_env,
            description=description,
            platforms=platforms or [],
            options=options,
        )

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a builder."""
        cls._builders.pop(name, None)

    @classmethod
    def get(cls, name: str) -> BuilderRegistration | None:
        """Get a builder registration by name, or None."""
        return cls._builders.get(name)

    @classmethod
    def names(cls) -> list[str]:
        """Get all registered builder names."""
        return list(cls._builders.keys())

    @classmethod
    def all(cls) -> dict[str, BuilderRegistration]:
        """Get all builder registrations, keyed by name."""
        return dict(cls._builders)

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations. Primarily for testing."""
        cls._builders.clear()


def builder(
    name: str,
    *,
    target_type: str,
    factory_class: type | None = None,
    requires_env: bool = False,
    description: str = "",
    platforms: list[str] | None = None,
    **options: Any,
) -> Callable[[type], type]:
    """Decorator to register a builder class.

    The decorated class must have a `create_target` static or class method
    that creates and returns a Target. Parameters are those of
    :meth:`BuilderRegistry.register`.

    Example:
        @builder("InstallSymlink", target_type="interface")
        class InstallSymlinkBuilder:
            @staticmethod
            def create_target(project, dest, source, *, name=None):
                target = Target(...)
                target._builder_name = "InstallSymlink"
                target._builder_data = {"dest": dest, "source": source}
                return target
    """

    def decorator(cls: type) -> type:
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
            platforms=platforms,
            **options,
        )

        # Store the registration name on the class for reference
        cast(Any, cls)._builder_name = name

        return cls

    return decorator
