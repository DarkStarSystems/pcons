# SPDX-License-Identifier: MIT
"""Tests for the debug/trace system."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

from pcons.core.debug import (
    SUBSYSTEMS,
    init_debug,
    is_enabled,
    reset_debug,
    trace,
    trace_value,
)


@pytest.fixture(autouse=True)
def reset_debug_state():
    """Reset debug state before and after each test."""
    reset_debug()
    yield
    reset_debug()


class TestInitDebug:
    """Tests for init_debug()."""

    def test_init_debug_single(self):
        """Test enabling a single subsystem."""
        init_debug("resolve")
        assert is_enabled("resolve")
        assert not is_enabled("subst")
        assert not is_enabled("env")

    def test_init_debug_multiple(self):
        """Test enabling multiple subsystems."""
        init_debug("resolve,subst")
        assert is_enabled("resolve")
        assert is_enabled("subst")
        assert not is_enabled("env")

    def test_init_debug_all(self):
        """Test enabling all subsystems."""
        init_debug("all")
        # All subsystems except "all" itself should be enabled
        for subsystem in SUBSYSTEMS - {"all"}:
            assert is_enabled(subsystem), f"{subsystem} should be enabled"
        # "all" itself is not a real subsystem
        assert not is_enabled("all")

    def test_init_debug_with_spaces(self):
        """Test that spaces around subsystem names are handled."""
        init_debug(" resolve , subst ")
        assert is_enabled("resolve")
        assert is_enabled("subst")

    def test_init_debug_case_insensitive(self):
        """Test that subsystem names are case-insensitive."""
        init_debug("RESOLVE,Subst")
        assert is_enabled("resolve")
        assert is_enabled("subst")

    def test_init_debug_invalid_subsystem_ignored(self):
        """Test that invalid subsystem names are silently ignored."""
        init_debug("resolve,invalid_subsystem,subst")
        assert is_enabled("resolve")
        assert is_enabled("subst")
        assert not is_enabled("invalid_subsystem")

    def test_init_debug_empty_string(self):
        """Test with empty string disables all subsystems."""
        init_debug("resolve")
        assert is_enabled("resolve")
        init_debug("")
        assert not is_enabled("resolve")

    def test_init_debug_none(self):
        """Test with None reads from environment."""
        # First enable something
        init_debug("resolve")
        assert is_enabled("resolve")

        # Reset and test with no env var
        reset_debug()
        with patch.dict(os.environ, {}, clear=True):
            init_debug(None)
        assert not is_enabled("resolve")

    def test_init_debug_from_env(self, monkeypatch):
        """Test reading from PCONS_DEBUG environment variable."""
        monkeypatch.setenv("PCONS_DEBUG", "configure,deps")
        init_debug()
        assert is_enabled("configure")
        assert is_enabled("deps")
        assert not is_enabled("resolve")


class TestIsEnabled:
    """Tests for is_enabled()."""

    def test_is_enabled_before_init(self):
        """Test is_enabled returns False before init_debug is called."""
        assert not is_enabled("resolve")
        assert not is_enabled("subst")

    def test_is_enabled_unknown_subsystem(self):
        """Test is_enabled returns False for unknown subsystems."""
        init_debug("resolve")
        assert not is_enabled("unknown")


class TestTrace:
    """Tests for trace() and trace_value()."""

    def test_trace_when_enabled(self, caplog):
        """Test that trace logs when subsystem is enabled."""
        init_debug("resolve")

        with caplog.at_level(logging.DEBUG, logger="pcons.resolve"):
            trace("resolve", "Test message: %s", "arg1")

        assert "Test message: arg1" in caplog.text

    def test_trace_when_disabled(self, caplog):
        """Test that trace does not log when subsystem is disabled."""
        init_debug("subst")  # Enable different subsystem

        with caplog.at_level(logging.DEBUG, logger="pcons.resolve"):
            trace("resolve", "Should not appear")

        assert "Should not appear" not in caplog.text

    def test_trace_value_when_enabled(self, caplog):
        """Test that trace_value logs with proper formatting."""
        init_debug("resolve")

        with caplog.at_level(logging.DEBUG, logger="pcons.resolve"):
            trace_value("resolve", "myvar", [1, 2, 3])

        assert "myvar = [1, 2, 3]" in caplog.text

    def test_trace_value_when_disabled(self, caplog):
        """Test that trace_value does not log when subsystem is disabled."""
        init_debug("subst")

        with caplog.at_level(logging.DEBUG, logger="pcons.resolve"):
            trace_value("resolve", "myvar", "should not appear")

        assert "myvar" not in caplog.text


class TestSubsystems:
    """Tests for subsystem constants."""

    def test_subsystems_frozenset(self):
        """Test that SUBSYSTEMS is a frozenset."""
        assert isinstance(SUBSYSTEMS, frozenset)

    def test_expected_subsystems(self):
        """Test that all expected subsystems are defined."""
        expected = {"configure", "resolve", "generate", "subst", "env", "deps", "all"}
        assert SUBSYSTEMS == expected


class TestResetDebug:
    """Tests for reset_debug()."""

    def test_reset_clears_state(self):
        """Test that reset_debug clears enabled subsystems."""
        init_debug("resolve,subst,env")
        assert is_enabled("resolve")
        assert is_enabled("subst")
        assert is_enabled("env")

        reset_debug()
        assert not is_enabled("resolve")
        assert not is_enabled("subst")
        assert not is_enabled("env")
