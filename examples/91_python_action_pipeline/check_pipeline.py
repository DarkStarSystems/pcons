# SPDX-License-Identifier: MIT
"""Run each built program and compare its bytes with what fed it.

Byte for byte, against the file the chain started from, because the fetched
documents differ on every run and no fixed string can be asserted against
them. Comparing against the input is the stronger check anyway: it proves the
bytes crossed fetch, embed, compile and link without being truncated or
re-encoded, which is exactly what a byte array in generated C can get wrong.

Each program takes its output path on the command line. Writing to a file
keeps the comparison honest on Windows, where stdout is a text stream and a
newline would come back as two bytes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

BUILD = Path("build")

#: program -> the file whose bytes it should reproduce
CHAINS = {
    "lorem": BUILD / "lorem.txt",
    "motto": BUILD / "motto.txt",
    "awkward": Path("src") / "awkward.bin",
}


def program(name: str) -> Path:
    """The built program, whatever this platform calls it."""
    for candidate in (BUILD / name, BUILD / f"{name}.exe"):
        if candidate.exists():
            return candidate
    raise SystemExit(f"{name} was not built")


def produced(name: str) -> bytes:
    """What the program writes when asked for its bytes."""
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "out.bin"
        done = subprocess.run(
            [str(program(name).resolve()), str(out)],
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            raise SystemExit(f"{name} exited {done.returncode}: {done.stderr!r}")
        return out.read_bytes()


for name, source in CHAINS.items():
    expected = source.read_bytes()
    if not expected:
        raise SystemExit(f"{source} is empty, so the comparison would prove nothing")
    found = produced(name)
    if found != expected:
        shared = min(len(found), len(expected))
        at = next((i for i in range(shared) if found[i] != expected[i]), shared)
        raise SystemExit(
            f"{name} wrote {len(found)} bytes, {source} holds {len(expected)}, "
            f"first difference at byte {at}"
        )

lorem, motto = CHAINS["lorem"].read_bytes(), CHAINS["motto"].read_bytes()
if lorem == motto:
    raise SystemExit("both fetches returned the same text, so neither proves anything")

awkward = CHAINS["awkward"].read_bytes()
if b"\x00" not in awkward or b"\xff" not in awkward:
    raise SystemExit("src/awkward.bin lost the bytes it exists to carry")

print("pipeline ok", file=sys.stdout)
