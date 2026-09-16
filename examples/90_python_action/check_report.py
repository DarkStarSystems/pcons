# SPDX-License-Identifier: MIT
"""Verify all three edges ran and each saw its own sources and arguments.

One decoration per environment, three calls, three different reports: that is
what a builder buys over a decorator that made one edge.
"""

from pathlib import Path

EXPECTED = {
    "build/report.txt": "word counts\na.txt: 9\nb.txt: 8\n",
    "build/report2.txt": "word counts, second set\nc.txt: 6\nd.txt: 7\n",
    "build/strict/report.txt": "word counts, strict\na.txt: 9\n",
}

for name, expected in EXPECTED.items():
    found = Path(name).read_text(encoding="utf-8")
    assert found == expected, f"{name} is {found!r}, expected {expected!r}"

assert len(set(EXPECTED.values())) == 3, "the three reports are not all different"

host = sorted(
    q.name for q in Path("build/pyact").iterdir() if q.suffix in {".py", ".pkl"}
)
assert host == ["report.args.pkl", "report.py", "report2.args.pkl"], host

strict = sorted(
    q.name for q in Path("build/strict/pyact").iterdir() if q.suffix in {".py", ".pkl"}
)
assert strict == ["report.args.pkl", "report.py"], strict

assert (
    Path("build/pyact/report.py").read_bytes()
    == Path("build/strict/pyact/report.py").read_bytes()
), "the two environments got different module bytes from one function"

print("report ok")
