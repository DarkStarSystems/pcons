# SPDX-License-Identifier: MIT
"""Tests for pcons core vars."""

from __future__ import annotations

import warnings
from pathlib import Path

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

    def test_default_and_type_together_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="not both"):
            get_var("TEST_VAR", "on", type=bool)  # type: ignore[call-overload]

    def test_agreeing_default_and_type_still_raises(self, clean_env) -> None:
        """The default's type already selects the conversion; type= is for
        when there is no default. Allowing both means two ways to say one
        thing, and a pair that can disagree."""
        with pytest.raises(ConfigureError, match="not both"):
            get_var("TEST_VAR", True, type=bool)  # type: ignore[call-overload]

    def test_unsupported_type_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="unsupported type"):
            get_var("TEST_VAR", type=list)  # type: ignore[call-overload]

    def test_non_class_type_raises(self, clean_env) -> None:
        """A value where a class belongs must be reported, not crash."""
        with pytest.raises(ConfigureError, match="unsupported type"):
            get_var("TEST_VAR", type=Path("/usr"))  # type: ignore[call-overload]

    def test_subscripted_generic_type_raises(self, clean_env) -> None:
        with pytest.raises(ConfigureError, match="unsupported type"):
            get_var("TEST_VAR", type=list[str])  # type: ignore[call-overload]

    def test_unsupported_default_raises(self, clean_env) -> None:
        with pytest.raises(
            ConfigureError, match="expected bool, int, float, str, Path"
        ):
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

    def test_path_from_env(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "/opt/tools")

        assert get_var("TEST_VAR", Path("/usr/local")) == Path("/opt/tools")

    def test_path_default(self, clean_env) -> None:
        assert get_var("TEST_VAR", Path("/usr/local")) == Path("/usr/local")

    def test_path_explicit_type(self, clean_env) -> None:
        assert get_var("TEST_VAR", type=Path) is None

        clean_env.setenv("TEST_VAR", "build/out")
        assert get_var("TEST_VAR", type=Path) == Path("build/out")

    def test_path_kept_relative(self, clean_env) -> None:
        """A relative path is taken verbatim, not resolved against the cwd."""
        clean_env.setenv("TEST_VAR", "dist")

        assert get_var("TEST_VAR", type=Path) == Path("dist")

    def test_path_rejects_empty(self, clean_env) -> None:
        clean_env.setenv("TEST_VAR", "  ")

        with pytest.raises(ConfigureError, match="not a valid path"):
            get_var("TEST_VAR", type=Path)

    def test_concrete_path_default_infers_path(self, clean_env) -> None:
        """Path("/x") is a PosixPath/WindowsPath, which must fold to Path."""
        clean_env.setenv("TEST_VAR", "/opt")
        default = Path("/usr")
        assert type(default) is not Path

        assert get_var("TEST_VAR", default) == Path("/opt")

    def test_bool_is_not_read_as_int(self, clean_env) -> None:
        """bool is an int subclass; inference must check bool first."""
        clean_env.setenv("TEST_VAR", "on")

        assert get_var("TEST_VAR", False) is True


class TestPconsVarsPayload:
    """PCONS_VARS carries raw `VAR=value` text, so its values are strings.
    A hand-written one holding a JSON bool or number must be reported."""

    @pytest.mark.parametrize("payload", ['{"TEST_VAR": true}', '{"TEST_VAR": 3}'])
    def test_non_string_value_raises(self, clean_env, payload) -> None:
        clean_env.setenv("PCONS_VARS", payload)

        with pytest.raises(ConfigureError, match="must be strings"):
            get_var("TEST_VAR", False)

    def test_non_string_value_raises_without_a_type(self, clean_env) -> None:
        clean_env.setenv("PCONS_VARS", '{"TEST_VAR": ["a"]}')

        with pytest.raises(ConfigureError, match="must be strings"):
            get_var("TEST_VAR")

    def test_an_unread_bad_value_is_left_alone(self, clean_env) -> None:
        """Only the variable actually asked for is checked."""
        clean_env.setenv("PCONS_VARS", '{"OTHER_VAR": true, "TEST_VAR": "ok"}')

        assert get_var("TEST_VAR", "x") == "ok"

    def test_invalid_json_warns_and_drops_the_overrides(self, clean_env) -> None:
        """pcons writes PCONS_VARS itself, so a payload that will not parse is
        someone else's doing. Warn and fall back rather than abort."""
        clean_env.setenv("PCONS_VARS", '{"TEST_VAR": ')
        clean_env.setenv("TEST_VAR", "from_env")

        with pytest.warns(UserWarning, match="invalid JSON"):
            assert get_var("TEST_VAR", "default") == "from_env"

    def test_invalid_json_is_parsed_once(self, clean_env) -> None:
        """The warning is not repeated for every variable the script reads."""
        clean_env.setenv("PCONS_VARS", "not json at all")

        with pytest.warns(UserWarning, match="invalid JSON"):
            assert get_var("TEST_VAR", "default") == "default"

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert get_var("OTHER_VAR", "default") == "default"

    def test_an_empty_payload_is_not_a_parse_error(self, clean_env) -> None:
        """PCONS_VARS='' is how the CLI says "no overrides"."""
        clean_env.setenv("PCONS_VARS", "")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert get_var("TEST_VAR", "default") == "default"
