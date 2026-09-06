# SPDX-License-Identifier: MIT
"""Verify the function ran at build time and saw its sources and kwargs."""

from pathlib import Path

report = Path("build/report.txt").read_text(encoding="utf-8")
expected = "word counts\na.txt: 9\nb.txt: 8\n"
assert report == expected, f"report is {report!r}, expected {expected!r}"
print("report ok")
