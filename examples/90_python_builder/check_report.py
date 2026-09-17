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
    q.name for q in Path("build/pybuilder").iterdir() if q.suffix in {".py", ".pkl"}
)
assert host == ["report.py", "report.txt.args.pkl", "report2.txt.args.pkl"], host
assert [q.name for q in Path("build/pybuilder/pcons-runner").iterdir()] == [
    "pcons-runner.py"
], "the runner's directory holds something besides the runner"
assert not Path("build/strict/pybuilder/pcons-runner").exists()

strict = sorted(
    q.name
    for q in Path("build/strict/pybuilder").iterdir()
    if q.suffix in {".py", ".pkl"}
)
assert strict == ["report.py", "report.txt.args.pkl"], strict

assert (
    Path("build/pybuilder/report.py").read_bytes()
    == Path("build/strict/pybuilder/report.py").read_bytes()
), "the two environments got different module bytes from one function"

print("report ok")
