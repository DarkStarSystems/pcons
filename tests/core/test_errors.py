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

    def test_bare_var_hints_dollar_escape(self):
        """Bare $VAR (no dot) should suggest $$ escaping."""
        err = MissingVariableError("ORIGIN")
        msg = str(err)
        assert "$$ORIGIN" in msg
        assert "literally" in msg

    def test_dotted_var_no_escape_hint(self):
        """Dotted $tool.var should NOT suggest $$ escaping."""
        err = MissingVariableError("link.badvar")
        msg = str(err)
        assert "$$" not in msg


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


class TestBuildErrorsAreReportedAsSuch:
    """A mistake in a build is a PconsError, which pcons reports as one line;
    anything else gets a traceback, as a bug in pcons should."""

    BUILTINS = (
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "FileNotFoundError",
    )

    def test_each_is_also_the_builtin_it_replaces(self):
        """So `except ValueError` around a pcons call keeps working."""
        import builtins

        from pcons.core import errors

        for name in self.BUILTINS:
            cls = getattr(errors, f"Pcons{name}")
            assert issubclass(cls, PconsError)
            assert issubclass(cls, getattr(builtins, name))

    def test_a_key_error_reads_as_its_message(self):
        from pcons.core.errors import PconsKeyError

        assert (
            str(PconsKeyError("Target 'app' not found.")) == "Target 'app' not found."
        )

    def test_pcons_raises_a_builtin_only_for_its_own_bugs(self):
        """A bare built-in raise is for pcons's own bugs and needs a comment
        above it saying why it isn't a user error; anything else raises the
        Pcons* error of the same name."""
        import ast
        from pathlib import Path

        import pcons

        unexplained = []
        package = Path(pcons.__file__).parent
        for path in sorted(package.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                call = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if not (isinstance(call, ast.Name) and call.id in self.BUILTINS):
                    continue
                if "not a user error" not in lines[node.lineno - 2]:
                    where = path.relative_to(package.parent).as_posix()
                    unexplained.append(f"{where}:{node.lineno} raises {call.id}")

        assert not unexplained, "\n".join(unexplained)
