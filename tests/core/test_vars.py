# SPDX-License-Identifier: MIT
"""Tests for pcons core vars."""

from __future__ import annotations

import pytest

from pcons import (
    get_var,
    get_variant,
)
from pcons.core.errors import ConfigureError
from pcons.core.vars import _clear_cli_vars


class TestGetVar:
    """Tests for get_var and get_variant functions."""

    def test_get_var_default(self, monkeypatch) -> None:
        """Test get_var returns default when not set."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", "default_value") == "default_value"

    def test_get_var_no_default_returns_none(self, monkeypatch) -> None:
        """Test get_var returns None when not set and no default given."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR") is None

    def test_get_var_with_none_default(self, monkeypatch) -> None:
        """Test get_var returns None when default is None."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.delenv("TEST_VAR", raising=False)

        assert get_var("TEST_VAR", None) is None

    def test_get_var_from_env(self, monkeypatch) -> None:
        """Test get_var reads from environment variable."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_VARS", raising=False)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        monkeypatch.setenv("TEST_VAR", "env_value")

        assert get_var("TEST_VAR", "default") == "env_value"

    def test_get_var_from_pcons_vars(self, monkeypatch) -> None:
        """Test get_var reads from PCONS_VARS JSON."""
        _clear_cli_vars()
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
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


@pytest.fixture
def clean_env(monkeypatch):
    """Isolate get_var from CLI vars and inherited environment."""
    _clear_cli_vars()
    monkeypatch.delenv("PCONS_VARS", raising=False)
    monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
    monkeypatch.delenv("TEST_VAR", raising=False)
    return monkeypatch


class TestGetVarTypes:
    """Type-aware conversion in get_var."""

    @pytest.mark.parametrize(
        "raw", ["1", "on", "yes", "true", "y", "ON", "True", " on "]
    )
    def test_bool_true_values(self, clean_env, raw) -> None:
        clean_env.setenv("TEST_VAR", raw)

        assert get_var("TEST_VAR", False) is True

    @pytest.mark.parametrize("raw", ["0", "off", "no", "false", "n", "OFF", "False"])
    def test_bool_false_values(self, clean_env, raw) -> None:
        clean_env.setenv("TEST_VAR", raw)

        assert get_var("TEST_VAR", True) is False

    def test_bool_rejects_other_values(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "maybe")

        with pytest.raises(ConfigureError, match="not a boolean"):
            get_var("TEST_VAR", False)

    def test_bool_default_returned_unparsed(self, clean_env) -> None:
        assert get_var("TEST_VAR", True) is True
        assert get_var("TEST_VAR", False) is False

    def test_explicit_type_without_default(self, clean_env) -> None:
        assert get_var("TEST_VAR", type=bool) is None

        clean_env.setenv("TEST_VAR", "on")
        assert get_var("TEST_VAR", type=bool) is True

    def test_explicit_type_matching_default(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "off")

        assert get_var("TEST_VAR", True, type=bool) is False

    def test_type_conflicting_with_default_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="conflicts with"):
            get_var("TEST_VAR", "on", type=bool)  # type: ignore[call-overload]

    def test_unsupported_type_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="unsupported type"):
            get_var("TEST_VAR", type=list)  # type: ignore[call-overload]

    def test_unsupported_default_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="expected bool, int, float or str"):
            get_var("TEST_VAR", [1])  # type: ignore[call-overload]

    def test_int_from_env(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "3")

        assert get_var("TEST_VAR", 2) == 3

    def test_int_default(self, clean_env) -> None:
        assert get_var("TEST_VAR", 2) == 2

    def test_int_rejects_non_numeric(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "high")

        with pytest.raises(ConfigureError, match="not a valid int"):
            get_var("TEST_VAR", 2)

    def test_float_from_pcons_vars(self, clean_env) -> None:
        clean_env.setenv("PCONS_VARS", '{"TEST_VAR": "1.5"}')

        assert get_var("TEST_VAR", 0.0) == 1.5

    def test_str_default_unchanged(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "ofx")

        assert get_var("TEST_VAR", "cuda") == "ofx"
        assert get_var("TEST_VAR", type=str) == "ofx"

    def test_bool_is_not_read_as_int(self, clean_env) -> None:
        """bool is an int subclass; inference must check bool first."""
        clean_env.setenv("TEST_VAR", "on")

        assert get_var("TEST_VAR", False) is True
