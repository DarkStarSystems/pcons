# SPDX-License-Identifier: MIT
"""Confirm every launcher ran, in order, in front of the real command."""

from pathlib import Path

records = [
    path.read_text(encoding="utf-8").split()
    for path in Path("build/launchers").glob("*.log")
]
assert records, "no records in build/launchers: no launcher ran"

# Each record is "<finish time> <label> <elapsed> <command>".
records.sort(key=lambda record: float(record[0]))
labels = [record[1] for record in records]

# The two tool launchers wrap each compile; the outer one finishes last.
compiles = [label for label in labels if label in ("cache", "timer")]
assert compiles == ["timer", "cache"], f"launchers ran out of order: {labels}"

# The third belongs to one command rather than to a tool.
assert labels.count("command") == 1, f"expected one command record, got {labels}"
print("launchers ok")
