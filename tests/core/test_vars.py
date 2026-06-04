# SPDX-License-Identifier: MIT
"""Tests for pcons core vars."""

from __future__ import annotations

from pcons import (
    get_var,
    get_variant,
)
from pcons.core.vars import _clear_cli_vars


class TestGetVar:
    """Tests for get_var and get_variant functions."""

    def test_get_var_default(self, monkeypatch) -> None:
        """Test get_var returns default when not set."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", "default_value") == "default_value"

    def test_get_var_no_default_returns_none(self, monkeypatch) -> None:
        """Test get_var returns None when not set and no default given."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR") is None

    def test_get_var_with_none_default(self, monkeypatch) -> None:
        """Test get_var returns None when default is None."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", None) is None

    def test_get_var_from_env(self, monkeypatch) -> None:
        """Test get_var reads from environment variable."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.setenv("TEST_VAR", "env_value")

        assert get_var("TEST_VAR", "default") == "env_value"

    def test_get_var_from_pcons_vars(self, monkeypatch) -> None:
        """Test get_var reads from PCONS_VARS JSON."""
        _clear_cli_vars()
        monkeypatch.setenv("PCONS_VARS", '{"TEST_VAR": "cli_value"}')
        monkeypatch.setenv("TEST_VAR", "env_value")  # Should be overridden

        assert get_var("TEST_VAR", "default") == "cli_value"

    def test_get_variant_default(self, monkeypatch) -> None:
        """Test get_variant returns default when not set."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "release"

    def test_get_variant_from_pcons_variant(self, monkeypatch) -> None:
        """Test get_variant reads from PCONS_VARIANT (CLI sets this)."""
        monkeypatch.setenv("PCONS_VARIANT", "debug")
        monkeypatch.delenv("VARIANT", raising=False)

        assert get_variant("release") == "debug"

    def test_get_variant_from_variant_env(self, monkeypatch) -> None:
        """Test get_variant falls back to VARIANT env var."""
        monkeypatch.delenv("PCONS_VARIANT", raising=False)
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("release") == "debug"

    def test_get_variant_pcons_variant_takes_precedence(self, monkeypatch) -> None:
        """Test PCONS_VARIANT takes precedence over VARIANT."""
        monkeypatch.setenv("PCONS_VARIANT", "release")
        monkeypatch.setenv("VARIANT", "debug")

        assert get_variant("default") == "release"
