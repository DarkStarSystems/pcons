# SPDX-License-Identifier: MIT
"""Tests for the recorded launcher entry, ``pcons.core.invocation``."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcons.core import invocation


@pytest.fixture(autouse=True)
def _reset_launcher_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the launcher entry's own state, which :func:`invocation.clear`
    deliberately leaves alone.
    """
    monkeypatch.setattr(invocation, "_launcher_entry", None)
    monkeypatch.setattr(invocation, "_launcher_entry_recorded", False)


class TestRecordLauncherEntry:
    def test_records_sys_path_zero_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = tmp_path / "somewhere"
        monkeypatch.setattr(sys, "path", [str(entry), "other"])

        invocation.record_launcher_entry()

        assert invocation.launcher_entry() == os.path.abspath(entry)

    def test_a_relative_entry_is_made_absolute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "path", ["relative"])

        invocation.record_launcher_entry()

        assert invocation.launcher_entry() == os.path.abspath("relative")

    def test_a_second_call_keeps_the_first_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        monkeypatch.setattr(sys, "path", [str(first)])

        invocation.record_launcher_entry()

        monkeypatch.setattr(sys, "path", [str(second)])
        invocation.record_launcher_entry()

        assert invocation.launcher_entry() == os.path.abspath(first)

    def test_safe_path_records_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "flags", SimpleNamespace(safe_path=True))
        monkeypatch.setattr(sys, "path", ["somewhere"])

        invocation.record_launcher_entry()

        assert invocation.launcher_entry() is None

    def test_nothing_recorded_before_the_first_call(self) -> None:
        assert invocation.launcher_entry() is None

    def test_clear_does_not_forget_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = tmp_path / "somewhere"
        monkeypatch.setattr(sys, "path", [str(entry)])
        invocation.record_launcher_entry()

        invocation.clear()

        assert invocation.launcher_entry() == os.path.abspath(entry)
