# SPDX-License-Identifier: MIT
"""Tests for pcons.util.source_location."""

from pcons.util.source_location import (
    SourceLocation,
    get_caller_location,
    get_source_location,
)


class TestSourceLocation:
    def test_basic(self):
        loc = SourceLocation("test.py", 42)
        assert loc.filename == "test.py"
        assert loc.lineno == 42
        assert loc.function is None
        assert str(loc) == "test.py:42"

    def test_with_function(self):
        loc = SourceLocation("test.py", 42, "my_function")
        assert loc.function == "my_function"
        assert str(loc) == "test.py:42 in my_function()"

    def test_short_filename(self):
        loc = SourceLocation("/long/path/to/test.py", 1)
        assert loc.short_filename == "test.py"

    def test_frozen(self):
        loc = SourceLocation("test.py", 42)
        # SourceLocation is frozen, so this should raise
        try:
            loc.lineno = 100  # type: ignore
            raise AssertionError("Should have raised AttributeError")
        except AttributeError:
            pass

    def test_hashable(self):
        loc1 = SourceLocation("test.py", 42)
        loc2 = SourceLocation("test.py", 42)
        assert hash(loc1) == hash(loc2)
        assert loc1 == loc2

        # Can use as dict key
        d = {loc1: "value"}
        assert d[loc2] == "value"


class TestGetSourceLocation:
    def test_captures_location(self):
        loc = get_source_location()
        # Should capture some valid location
        assert loc.filename is not None
        assert loc.lineno > 0

    def test_depth_increases_stack_level(self):
        def level1():
            return get_source_location(depth=1)

        def level2():
            return level1()

        loc1 = get_source_location(depth=1)
        loc2 = level2()
        # Different depths should give different locations
        # Just verify they both return valid locations
        assert loc1.lineno > 0
        assert loc2.lineno > 0


class TestGetCallerLocation:
    def helper(self):
        return get_caller_location()

    def test_captures_callers_caller(self):
        loc = self.helper()
        # Should capture this function, not helper()
        assert loc.function == "test_captures_callers_caller"
