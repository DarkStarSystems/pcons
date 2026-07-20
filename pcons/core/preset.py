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
    from collections.abc import Callable

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
        arch: Target architecture this preset selects, if any. Only presets
            with category "arch" (the ``set_target_arch`` knob) record it on
            the environment as ``env.target_arch``; on other categories it is
            metadata (docs/presets.md, "Preset application").
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
    """A contributed preset: identity + a function.

    For a **declarative** preset (the common case), ``fn`` is a resolver
    ``(toolchain) -> contributions | None``. For an **imperative** escape-hatch
    preset, ``fn`` receives the *environment* and may do anything (remove or
    override flags, etc.); it is responsible for self-describing via
    ``description`` so ``explain()`` can report that it ran.
    """

    name: str
    category: str
    fn: Callable[[Any], Any]
    description: str = ""
    imperative: bool = False

    @property
    def scope(self) -> str | None:
        """The namespace before the '/', or None for an (unscoped) name."""
        return self.name.split("/", 1)[0] if "/" in self.name else None


_PRESET_REGISTRY: dict[str, RegisteredPreset] = {}


def register_preset(
    name: str,
    fn: Callable[[Any], Any],
    *,
    category: str = "feature",
    description: str = "",
    imperative: bool = False,
) -> None:
    """Register a contributed preset.

    Args:
        name: Namespaced ``"scope/name"`` (bare names are reserved for pcons).
        fn: Declarative resolver ``(toolchain) -> contributions | None`` (the
            default), or — when ``imperative=True`` — a function ``(env) -> None``
            that mutates the environment directly (the escape hatch for needs
            that aren't just adding flags: removing/overriding a flag, etc.).
        category: Preset category (only ``"feature"`` is resolved by
            ``apply_preset`` today).
        description: Human-readable summary. Required in spirit for imperative
            presets, since it's what ``explain()`` reports for them.
        imperative: If True, ``fn`` receives the environment and may do anything.
    """
    if "/" not in name:
        logger.warning(
            "Preset '%s' is registered without a scope; contributed presets "
            "should be namespaced as 'scope/name' (bare names are reserved for "
            "pcons built-ins).",
            name,
        )
    _PRESET_REGISTRY[name] = RegisteredPreset(
        name, category, fn, description, imperative
    )


def preset(
    name: str,
    *,
    category: str = "feature",
    description: str = "",
    imperative: bool = False,
) -> Callable[[Callable[[Any], Any]], Callable[[Any], Any]]:
    """Decorator form of :func:`register_preset` over the preset's function."""

    def decorate(fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        register_preset(
            name, fn, category=category, description=description, imperative=imperative
        )
        return fn

    return decorate


def list_presets() -> list[RegisteredPreset]:
    """Return the registered (contributed) presets, sorted by name."""
    return sorted(_PRESET_REGISTRY.values(), key=lambda p: p.name)


def is_registered_preset(name: str) -> bool:
    """Whether *name* is a contributed preset (regardless of applicability)."""
    return name in _PRESET_REGISTRY


def resolve_registered_feature(name: str, toolchain: Any) -> Preset | None:
    """Resolve a contributed *declarative feature* preset for a toolchain."""
    entry = _PRESET_REGISTRY.get(name)
    if entry is None or entry.imperative or entry.category != "feature":
        return None
    contributions = entry.fn(toolchain)
    if not contributions:
        return None
    return Preset(
        name=name, category=entry.category, contributions=tuple(contributions)
    )


def apply_imperative_preset(name: str, env: Any) -> str | None:
    """Run an imperative escape-hatch preset against *env*.

    Returns the preset's description (for ``explain()``) if it ran, else None
    (the name isn't a registered imperative preset).
    """
    entry = _PRESET_REGISTRY.get(name)
    if entry is None or not entry.imperative:
        return None
    entry.fn(env)
    return entry.description or name
