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

from dataclasses import dataclass


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
