# SPDX-License-Identifier: MIT
"""The example harness's skip rules that probe the host (tests/test_examples.py)."""

from __future__ import annotations

import shlex
import sys

from tests.test_examples import should_skip

PYTHON = shlex.quote(sys.executable)


def _skip(*commands: str) -> str | None:
    return should_skip({"skip": {"require_succeeds": list(commands)}})


def test_a_command_that_succeeds_runs_the_example() -> None:
    assert _skip(f"{PYTHON} -c pass") is None


def test_a_command_that_fails_skips_with_its_own_complaint() -> None:
    reason = _skip(f"{PYTHON} -c \"import sys; sys.exit('missing Metal Toolchain')\"")
    assert reason is not None
    assert reason.endswith(": missing Metal Toolchain")


def test_a_missing_program_is_named() -> None:
    assert _skip("no-such-tool-for-pcons --version") == (
        "Required tool 'no-such-tool-for-pcons' not found"
    )
