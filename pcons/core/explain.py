# SPDX-License-Identifier: MIT
"""Explain where a tool's flags came from.

Provenance is *derived*, not recorded per flag: an :class:`~pcons.core.preset.Preset`
carries the exact tokens it contributes, and an environment keeps the ordered
list of presets applied to it. :func:`explain` replays that list against a tool's
current flag/define lists and attributes each token to the preset that added it.
Tokens not produced by any preset (toolchain defaults, or direct
``env.cc.flags.append(...)``) are labelled ``(manual)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.preset import Preset


@dataclass(frozen=True)
class ExplainRow:
    """One token of a tool variable and where it came from.

    ``source`` is the contributing preset's name, or ``None`` if the token was
    not produced by any preset (a toolchain default or a manual edit).
    """

    tool: str
    var: str  # "flags", "defines", or "cmd"
    token: str
    source: str | None
    category: str | None


@dataclass(frozen=True)
class Explanation:
    """The attributed tokens of one or more tools, with a readable ``str``.

    ``imperative`` lists ``(name, description)`` of any imperative escape-hatch
    presets that ran — these mutate the environment directly, so their effect
    can't be attributed token-by-token; they're reported as a trailing note.
    """

    rows: tuple[ExplainRow, ...]
    imperative: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rows) or bool(self.imperative)

    def __str__(self) -> str:
        lines: list[str] = []
        if self.rows:
            # Group rows by "tool.var", preserving first-seen order.
            groups: dict[str, list[ExplainRow]] = {}
            for row in self.rows:
                groups.setdefault(f"{row.tool}.{row.var}", []).append(row)

            width = max(len(r.token) for r in self.rows)
            for key, rows in groups.items():
                lines.append(f"{key}:")
                for r in rows:
                    if r.source is None:
                        origin = "<- (manual)"
                    else:
                        origin = f"<- {r.source} ({r.category})"
                        if r.var == "cmd":
                            origin += " [replaced]"
                    lines.append(f"  {r.token.ljust(width)}  {origin}")
        if self.imperative:
            lines.append("imperative presets (ran; effect not attributable):")
            for name, desc in self.imperative:
                lines.append(f"  {name} - {desc}" if desc else f"  {name}")
        return "\n".join(lines) if lines else "(no flags)"


def _attribute(
    actual: Sequence[Any],
    expected: Sequence[tuple[str, str, str]],
) -> list[tuple[str, str | None, str | None]]:
    """Match preset-contributed tokens against a tool's actual token list.

    Presets only ever append, in order, so their tokens form a subsequence of
    the actual list. Walk the actual list greedily, advancing through
    ``expected`` on each match; unmatched tokens are manual.
    """
    result: list[tuple[str, str | None, str | None]] = []
    j = 0
    for tok in actual:
        if j < len(expected) and expected[j][0] == tok:
            _, source, category = expected[j]
            result.append((tok, source, category))
            j += 1
        else:
            result.append((tok, None, None))
    return result


def explain(
    applied_presets: Sequence[Preset],
    tools: dict[str, dict[str, object]],
    imperative: Sequence[tuple[str, str]] = (),
) -> Explanation:
    """Build an :class:`Explanation` for the given tools.

    Args:
        applied_presets: Presets applied to the environment, in order.
        tools: ``{tool_name: {"flags": [...], "defines": [...], "cmd": value}}``
            snapshot of each tool's current values.
        imperative: ``(name, description)`` of imperative presets that ran.
    """
    rows: list[ExplainRow] = []
    for tool_name, values in tools.items():
        for var in ("flags", "defines"):
            actual = values.get(var)
            if not isinstance(actual, list) or not actual:
                continue
            expected: list[tuple[str, str, str]] = [
                (token, preset.name, preset.category)
                for preset in applied_presets
                for contribution in preset.contributions
                if contribution.tool == tool_name
                for token in getattr(contribution, var)
            ]
            for token, source, category in _attribute(actual, expected):
                rows.append(ExplainRow(tool_name, var, token, source, category))

        # cmd is replaced (not appended); attribute to the last preset that set
        # it, but only show a row when a preset actually replaced the command.
        cmd = values.get("cmd")
        if isinstance(cmd, str):
            # (preset_name, category, cmd_value) of the last preset to set cmd
            replaced: tuple[str, str, str] | None = None
            for preset in applied_presets:
                for contribution in preset.contributions:
                    if contribution.tool == tool_name and contribution.cmd is not None:
                        replaced = (preset.name, preset.category, contribution.cmd)
            if replaced is not None and cmd == replaced[2]:
                rows.append(ExplainRow(tool_name, "cmd", cmd, replaced[0], replaced[1]))

    return Explanation(tuple(rows), tuple(imperative))
