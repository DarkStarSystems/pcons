# SPDX-License-Identifier: MIT
"""Confirm every launcher ran, in order, in front of the real command."""

from pathlib import Path

lines = Path("build/launchers.log").read_text(encoding="utf-8").splitlines()
labels = [line.split()[0] for line in lines]

# The two tool launchers wrap each compile; the outer one finishes last.
compiles = [label for label in labels if label in ("cache", "timer")]
assert compiles == ["timer", "cache"], f"launchers ran out of order: {labels}"

# The third belongs to one command rather than to a tool.
assert labels.count("command") == 1, f"expected one command line, got {labels}"
print("launchers ok")
