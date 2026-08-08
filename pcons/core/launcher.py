# SPDX-License-Identifier: MIT
"""Command launchers: tokens that run *in front of* an edge's command.

``ccache`` before the compiler, ``time`` before anything worth measuring, a
persistent-worker client. See the Command Launchers section of the user guide
for how they are declared.

Kept as its own token list rather than folded into a tool's ``cmd``, for two
reasons that are easy to undo by accident: commands stay lists until a
generator quotes them, so ``"ccache gcc"`` merged into ``cmd`` becomes one
shell word and fails; and keeping it separate lets each generator decide,
which is how ``compile_commands.json`` reports the compiler an IDE wants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.environment import Environment


def resolve_launcher(
    env: Environment,
    tool_name: str | None,
    extra: Sequence[str] | str | None = None,
) -> list[str]:
    """The launcher tokens for an edge run by *tool_name*, expanded.

    The tool's launcher runs outermost, then whatever this one edge asked for
    itself (*extra*), then the command. Empty when neither is set, which is
    the common case.
    """
    tokens: list[str] = []
    if tool_name is not None:
        tool_config = getattr(env, tool_name, None)
        if tool_config is not None:
            tokens.extend(_as_tokens(getattr(tool_config, "launcher", None)))
    tokens.extend(_as_tokens(extra))
    if not tokens:
        return []
    return [str(token) for token in env.subst_list(tokens)]


def _as_tokens(value: Sequence[str] | str | None) -> list[str]:
    """Normalize a launcher setting; a bare program name is tolerated."""
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)
