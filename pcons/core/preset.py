# SPDX-License-Identifier: MIT
"""Declarative preset model.

A :class:`Preset` is a named, immutable bundle of tool contributions — the one
concept that build variants, semantic feature presets, and cross-compilation
targets all reduce to. Applying a preset (:meth:`Environment.apply`) extends the
relevant tool flag/define lists, optionally swaps a tool command, and records
the preset so its effect can later be explained.

The core stays tool-agnostic: a preset carries opaque tokens only. Toolchains
build presets (``make_variant_preset``/``make_feature_preset``/
``make_target_preset``); the core just applies them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContribution:
    """What a preset contributes to one tool.

    ``flags`` and ``defines`` extend the tool's existing lists; ``cmd``, if
    given, replaces the tool's command (used by cross-presets that swap the
    compiler binary, e.g. ``cc`` → ``emcc``).
    """

    tool: str
    flags: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    cmd: str | None = None


@dataclass(frozen=True)
class Preset:
    """A named bundle of tool contributions.

    Attributes:
        name: Preset name (e.g. "release", "warnings", "wasm32-emscripten").
        category: "variant", "feature", "target", or "arch".
        contributions: Per-tool flag/define/cmd contributions.
        exclusive_group: Presets sharing a group are mutually exclusive on a
            single environment (variants share "build_variant"); applying a
            second, differently-named preset in the group raises. Clone the
            environment to build multiple variants.
        arch: Target architecture this preset selects, if any. Recorded on the
            environment as bookkeeping (``env.target_arch``).
    """

    name: str
    category: str
    contributions: tuple[ToolContribution, ...] = ()
    exclusive_group: str | None = None
    arch: str | None = None


# =============================================================================
# Contributed feature-preset registry
#
# Built-in feature presets live in the toolchains (their FEATURE_PRESETS). The
# registry holds *contributed* presets — identity + a resolver that produces
# contributions for a given toolchain — never raw flags. Resolution is
# toolchain-first, then registry. Names are namespaced "scope/name"; bare names
# are reserved for pcons built-ins. See docs/presets.md.
# =============================================================================


@dataclass(frozen=True)
class RegisteredPreset:
    """A contributed preset: identity + a per-toolchain resolver."""

    name: str
    category: str
    resolver: Callable[[Any], Sequence[ToolContribution] | None]
    description: str = ""

    @property
    def scope(self) -> str | None:
        """The namespace before the '/', or None for an (unscoped) name."""
        return self.name.split("/", 1)[0] if "/" in self.name else None


_PRESET_REGISTRY: dict[str, RegisteredPreset] = {}


def register_preset(
    name: str,
    resolver: Callable[[Any], Sequence[ToolContribution] | None],
    *,
    category: str = "feature",
    description: str = "",
) -> None:
    """Register a contributed preset.

    Args:
        name: Namespaced ``"scope/name"`` (bare names are reserved for pcons).
        resolver: ``(toolchain) -> contributions | None`` — returns the tool
            contributions for that toolchain, or None if it doesn't apply.
        category: Preset category (only ``"feature"`` is resolved by
            ``apply_preset`` today).
        description: Optional human-readable summary.
    """
    if "/" not in name:
        logger.warning(
            "Preset '%s' is registered without a scope; contributed presets "
            "should be namespaced as 'scope/name' (bare names are reserved for "
            "pcons built-ins).",
            name,
        )
    _PRESET_REGISTRY[name] = RegisteredPreset(name, category, resolver, description)


def preset(
    name: str, *, category: str = "feature", description: str = ""
) -> Callable[
    [Callable[[Any], Sequence[ToolContribution] | None]],
    Callable[[Any], Sequence[ToolContribution] | None],
]:
    """Decorator form of :func:`register_preset` over a resolver function."""

    def decorate(
        fn: Callable[[Any], Sequence[ToolContribution] | None],
    ) -> Callable[[Any], Sequence[ToolContribution] | None]:
        register_preset(name, fn, category=category, description=description)
        return fn

    return decorate


def list_presets() -> list[RegisteredPreset]:
    """Return the registered (contributed) presets, sorted by name."""
    return sorted(_PRESET_REGISTRY.values(), key=lambda p: p.name)


def is_registered_preset(name: str) -> bool:
    """Whether *name* is a contributed preset (regardless of applicability)."""
    return name in _PRESET_REGISTRY


def resolve_registered_feature(name: str, toolchain: Any) -> Preset | None:
    """Resolve a contributed *feature* preset for a toolchain, or None."""
    entry = _PRESET_REGISTRY.get(name)
    if entry is None or entry.category != "feature":
        return None
    contributions = entry.resolver(toolchain)
    if not contributions:
        return None
    return Preset(
        name=name, category=entry.category, contributions=tuple(contributions)
    )
