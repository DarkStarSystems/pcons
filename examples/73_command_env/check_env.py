# SPDX-License-Identifier: MIT
"""Verify the per-command variable reached one command and not its neighbour."""

from pathlib import Path

with_var = Path("build/with_var.txt").read_text()
without_var = Path("build/without_var.txt").read_text()
assert with_var == "from-env-vars", f"env_vars did not arrive: {with_var!r}"
assert without_var == "unset", f"env_vars leaked to a neighbour: {without_var!r}"
print("env ok")
