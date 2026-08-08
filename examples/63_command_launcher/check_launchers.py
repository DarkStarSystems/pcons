# SPDX-License-Identifier: MIT
"""Confirm both launchers ran, in order, in front of the real compile."""

from pathlib import Path

lines = Path("build/launchers.log").read_text(encoding="utf-8").splitlines()
labels = [line.split()[0] for line in lines]

assert labels.count("cache") == 1, f"expected one cache line, got {labels}"
assert labels.count("timer") == 1, f"expected one timer line, got {labels}"
# The outer launcher wraps the inner one, so it finishes second and logs last.
assert labels == ["timer", "cache"], f"launchers ran out of order: {labels}"
print("launchers ok")
