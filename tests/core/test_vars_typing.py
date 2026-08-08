# SPDX-License-Identifier: MIT
"""Static contract for get_var's overloads.

A call the runtime can only answer with ConfigureError should not type-check.
The pair `default=` plus `type=` is exactly that: the default already selects
the conversion, so a type= alongside it is either redundant or a conflict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

mypy_api = pytest.importorskip("mypy.api")

REJECTED = """
from pathlib import Path
from pcons import get_var

get_var("X", 1, type=float)
get_var("X", True, type=int)
get_var("X", 1, type=bool)
get_var("X", "s", type=Path)
get_var("X", Path("/usr"), type=str)
get_var("X", 2, type=int)
"""

ACCEPTED = """
from pathlib import Path
from pcons import get_var

raw: str | None = get_var("X")
flag: bool = get_var("X", False)
level: int = get_var("X", 2)
scale: float = get_var("X", 1.0)
port: str = get_var("X", "ofx")
prefix: Path = get_var("X", Path("/usr/local"))
maybe_flag: bool | None = get_var("X", type=bool)
maybe_prefix: Path | None = get_var("X", type=Path)
"""


def _check(tmp_path: Path, source: str) -> tuple[str, int]:
    src = tmp_path / "snippet.py"
    src.write_text(source)
    out, _err, status = mypy_api.run(
        [str(src), "--no-error-summary", "--no-incremental", "--follow-imports=silent"]
    )
    return out, status


def test_a_default_with_a_type_does_not_type_check(tmp_path: Path) -> None:
    out, status = _check(tmp_path, REJECTED)

    assert status != 0, out
    rejected = [line for line in out.splitlines() if "call-overload" in line]
    assert len(rejected) == 6, out


def test_the_documented_calls_type_check(tmp_path: Path) -> None:
    out, status = _check(tmp_path, ACCEPTED)

    assert status == 0, out
