# SPDX-License-Identifier: MIT
"""The action's output must be right however it was run."""

from pathlib import Path

report = Path("build/report.txt").read_text(encoding="utf-8").strip()
assert report == "alpha, beta, gamma", f"unexpected report: {report!r}"
print("report ok")
