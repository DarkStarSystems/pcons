# SPDX-License-Identifier: MIT
"""Tests for pcons.core.errors."""

from pcons.core.errors import (
    BuilderError,
    CircularReferenceError,
    ConfigureError,
    DependencyCycleError,
    GenerateError,
    MissingSourceError,
    MissingVariableError,
    PconsError,
    SubstitutionError,
    ToolNotFoundError,
)
from pcons.util.source_location import SourceLocation


class TestPconsError:
    def test_basic_error(self):
        err = PconsError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.message == "something went wrong"
        assert err.location is None

    def test_error_with_location(self):
        loc = SourceLocation("pcons-build.py", 42, "configure")
        err = PconsError("something went wrong", location=loc)
        assert "pcons-build.py:42" in str(err)
        assert "something went wrong" in str(err)
        assert err.location == loc

    def test_inheritance(self):
        assert issubclass(ConfigureError, PconsError)
        assert issubclass(GenerateError, PconsError)
        assert issubclass(SubstitutionError, PconsError)
        assert issubclass(MissingVariableError, SubstitutionError)
        assert issubclass(CircularReferenceError, SubstitutionError)
        assert issubclass(DependencyCycleError, PconsError)
        assert issubclass(ToolNotFoundError, ConfigureError)
        assert issubclass(BuilderError, PconsError)


class TestMissingVariableError:
    def test_format(self):
        err = MissingVariableError("FOO")
        assert "FOO" in str(err)
        assert "undefined variable" in str(err)
        assert err.variable == "FOO"

    def test_with_location(self):
        loc = SourceLocation("pcons-build.py", 10)
        err = MissingVariableError("BAR", location=loc)
        assert "pcons-build.py:10" in str(err)
        assert "BAR" in str(err)


class TestCircularReferenceError:
    def test_format(self):
        err = CircularReferenceError(["A", "B", "C", "A"])
        assert "A -> B -> C -> A" in str(err)
        assert err.chain == ["A", "B", "C", "A"]


class TestDependencyCycleError:
    def test_format(self):
        err = DependencyCycleError(["foo.o", "bar.o", "foo.o"])
        assert "foo.o -> bar.o -> foo.o" in str(err)
        assert err.cycle == ["foo.o", "bar.o", "foo.o"]


class TestMissingSourceError:
    def test_format(self):
        err = MissingSourceError("/path/to/missing.cpp")
        assert "/path/to/missing.cpp" in str(err)
        assert err.path == "/path/to/missing.cpp"


class TestToolNotFoundError:
    def test_format(self):
        err = ToolNotFoundError("gcc")
        assert "gcc" in str(err)
        assert err.tool == "gcc"
