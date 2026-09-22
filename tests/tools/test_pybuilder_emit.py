# SPDX-License-Identifier: MIT
"""Tests for the generate-time half of PyBuilder, pcons.tools.pybuilder."""

from __future__ import annotations

import functools
import importlib.util
import inspect
import os
import pickle
import textwrap
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pcons.core.collate import write_bytes_if_changed
from pcons.core.project import Project
from pcons.tools.pybuilder import (
    MODULE_PREFIX,
    PyBuilder,
    PyBuilderError,
    ValidatedFunction,
    _claim,
    _reserved_names,
    check_arguments,
    emit_args,
    emit_module,
    function_source,
    validate,
)
from pcons.util.pybuilder import PROTOCOL_VERSION, run
from pcons.util.source_location import SourceLocation

SCRIPT_GLOBAL = "visible from the build script only"


def keep(*args: object, **kwargs: object):
    """A decorator that returns the function untouched."""

    def wrap(fn):
        return fn

    return wrap


@keep()
def decorated(targets, sources):
    return len(sources) + len(targets)


def documented(targets, sources):
    """A docstring."""

    class Inner:
        value = 1

    def nested():
        return Inner.value

    return nested()


if True:

    def indented(targets, sources):
        return "indented"


def factory(unused):
    @keep()
    def inner(targets, sources):
        return "from a factory"

    return inner


def closing_over(env):
    def inner(targets, sources):
        return env

    return inner


class Holder:
    def method(self, targets, sources):
        return 1


async def coroutine(targets, sources):
    return 1


def uses_a_script_global(targets, sources):
    return SCRIPT_GLOBAL


def writes_sources(targets, sources):
    with open(targets[0], "w") as out:
        out.write("|".join(sources))


def takes_arguments(targets, sources, n=0, handle=None):
    """A body with arguments, for the tests that pass some."""
    return n, handle


def defaults_from_the_script(targets, sources, label=SCRIPT_GLOBAL):
    return label


def annotated(targets, sources, out: Path | None = None) -> Path | None:
    return out


def uses_dunder_file(targets, sources):
    return __file__


def emit_both(
    fn: Any,
    *,
    project: Project,
    env: Any,
    name: str,
    target: object = None,
    kwargs: Mapping[str, Any],
    sys_path: list[str] | None = None,
) -> tuple[Path, Path]:
    """The three calls one decoration makes, in order, then the writes.

    Validation, the module and the pickle run on different clocks, and the
    tests below that only ask what landed in the build directory want all
    three. The writes are the ones resolving the edge performs, done here
    directly so these tests need no edge.

    *sys_path* defaults to None: these tests are not about what a real
    decoration would capture, only ``env.PyBuilder()`` itself does that.
    """
    function = validate(fn, project=project)
    payload = check_arguments(function, kwargs=kwargs, sys_path=sys_path)
    module_rel, module_bytes = emit_module(function, project=project, env=env)
    args_rel = emit_args(project=project, env=env, name=name, target=target or name)
    root = project.top_path_resolver.project_root
    if module_bytes is not None:
        write_bytes_if_changed(root / module_rel, module_bytes)
    write_bytes_if_changed(root / args_rel, payload)
    return module_rel, args_rel


def run_emit(
    project: Project,
    env: Any,
    fn: Any,
    name: str = "report",
    target: object = None,
    kwargs: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Every emit in this file goes through here, on one line.

    ``validate`` records the caller's location in the generated module's
    header, so calls from two different lines would differ in content for
    that reason alone and no mtime test could say anything.

    *target* defaults to *name* itself, which keeps every call that only
    cares about the edge's name working the way it did before the pickle
    started following the target's own path.
    """
    return emit_both(
        fn, project=project, env=env, name=name, target=target, kwargs=kwargs or {}
    )


def load(path: Path, name: str) -> ModuleType:
    """Import a written file as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_module(tmp_path: Path, name: str, text: str) -> ModuleType:
    """Write *text* as a module and import it."""
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return load(path, name)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project("p", root_dir=tmp_path)


@pytest.fixture
def env(project: Project) -> Any:
    return project.Environment()


DECORATOR_SHAPES = """
def keep(*args, **kwargs):
    def wrap(fn):
        return fn
    return wrap


def plain(targets, sources):
    return 1


@keep()
def once(targets, sources):
    return 1


@keep()
@keep("a")
def twice(targets, sources):
    return 1


@keep(
    "a",
    "b",
)
def multiline(targets, sources):
    return 1


# A comment above the decoration.
@keep()
def commented(targets, sources):
    return 1
"""


class TestFunctionSource:
    def test_the_decorators_are_dropped(self) -> None:
        assert function_source(decorated).startswith("def decorated(")

    def test_every_decorator_shape_gives_the_same_body(self, tmp_path: Path) -> None:
        module = write_module(tmp_path, "shapes", DECORATOR_SHAPES)
        bodies = {
            name: function_source(getattr(module, name))
            for name in ("plain", "once", "twice", "multiline", "commented")
        }

        for name, body in bodies.items():
            assert body.startswith(f"def {name}("), body
        assert len({body.split("\n", 1)[1] for body in bodies.values()}) == 1

    def test_a_docstring_and_nested_definitions_survive(self) -> None:
        source = function_source(documented)

        assert '"""A docstring."""' in source
        assert "class Inner:" in source
        assert "def nested():" in source

    def test_a_function_in_a_block_is_dedented(self) -> None:
        source = function_source(indented)

        assert source.startswith("def indented(")
        assert "\n    return" in source

    def test_a_function_nested_in_a_factory_extracts(self) -> None:
        source = function_source(factory(None))

        assert source.startswith("def inner(")
        assert 'return "from a factory"' in source

    def test_a_coroutine_is_refused(self) -> None:
        with pytest.raises(PyBuilderError, match="coroutine"):
            function_source(coroutine)

    def test_a_function_with_no_readable_source_is_refused(self) -> None:
        namespace: dict[str, Any] = {}
        exec("def render(targets, sources):\n    return 1\n", namespace)  # noqa: S102

        with pytest.raises(PyBuilderError, match="written out in a build script"):
            function_source(namespace["render"])


class TestRejections:
    def test_a_lambda(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="lambda"):
            run_emit(project, env, lambda targets, sources: None)

    def test_a_closure_names_its_free_variables(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="reads env from"):
            run_emit(project, env, closing_over(env))

    def test_a_builtin(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="written in a build script"):
            run_emit(project, env, len)

    def test_a_partial(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="functools.partial"):
            run_emit(project, env, functools.partial(writes_sources, []))

    def test_a_method(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="class body"):
            run_emit(project, env, Holder.method)

    def test_a_bound_method(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="written in a build script"):
            run_emit(project, env, Holder().method)

    def test_a_body_reading_a_script_global(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="SCRIPT_GLOBAL"):
            run_emit(project, env, uses_a_script_global)

    def test_an_attribute_name_is_not_mistaken_for_a_global(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module = write_module(
            tmp_path,
            "attrs",
            """
            load = "a name this module also defines"


            def render(targets, sources):
                with open(targets[0], "w") as out:
                    out.load = 1
                    out.write("ok")
            """,
        )

        run_emit(project, env, module.render)

    def test_a_coroutine_reaches_the_error_through_emit(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="coroutine"):
            run_emit(project, env, coroutine)

    def test_a_refused_function_does_not_claim_its_module(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="coroutine"):
            run_emit(project, env, coroutine)

        module_rel, _ = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/pybuilder/writes_sources.py")

    def test_a_default_reading_a_script_global(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="SCRIPT_GLOBAL"):
            run_emit(project, env, defaults_from_the_script)

    def test_an_annotation_reading_a_script_global_is_fine(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The generated module never evaluates an annotation."""
        module_rel, _ = run_emit(project, env, annotated)
        text = (tmp_path / module_rel).read_text(encoding="utf-8")

        assert "from __future__ import annotations" in text
        assert "out: Path | None = None" in text
        assert hasattr(load(tmp_path / module_rel, "gen_annotated"), "annotated")

    def test_a_body_reading_dunder_file(self, project: Project, env: Any) -> None:
        with pytest.raises(PyBuilderError, match="names the generated module"):
            run_emit(project, env, uses_dunder_file)

    def test_an_unpicklable_kwarg_names_the_key(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyBuilderError, match="cannot pickle argument handle"):
            run_emit(
                project,
                env,
                takes_arguments,
                kwargs={"n": 1, "handle": lambda: None},
            )


class TestEmit:
    def test_the_returned_paths_are_build_relative(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/pybuilder/writes_sources.py")
        assert args_rel == Path("build/pybuilder/report.args.pkl")
        assert (tmp_path / module_rel).is_file()
        assert (tmp_path / args_rel).is_file()

    def test_the_module_holds_the_function_and_its_origin(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, _ = run_emit(project, env, writes_sources)
        text = (tmp_path / module_rel).read_text(encoding="utf-8")

        assert text.startswith("# SPDX-License-Identifier: MIT\n")
        assert text.splitlines()[1].endswith(
            "from test_pybuilder_emit.py. Do not edit."
        )
        assert "from __future__ import annotations" in text
        assert "def writes_sources(targets, sources):" in text
        assert "@" not in text

    def test_the_origin_carries_no_line_number(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """A line inserted above the decoration must not rewrite the module.

        The two emits below sit on different lines of this file, which is
        what a line inserted above one of them would do to the other.
        """
        module_rel, _ = emit_both(
            writes_sources, project=project, env=env, name="report", kwargs={}
        )
        os.utime(tmp_path / module_rel, (0, 0))

        Project._clear_tree()
        again = Project("again", root_dir=tmp_path)
        emit_both(
            writes_sources,
            project=again,
            env=again.Environment(),
            name="report",
            kwargs={},
        )

        assert (tmp_path / module_rel).stat().st_mtime == 0

    def test_the_origin_is_relative_to_the_project_root(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        caller = write_module(
            tmp_path,
            "caller",
            """
            from pcons.tools.pybuilder import emit_module, validate


            def render(targets, sources):
                return 1


            def emit_it(project, env):
                function = validate(render, project=project)
                return emit_module(function, project=project, env=env)
            """,
        )

        _, module_bytes = caller.emit_it(project, env)
        header = module_bytes.decode("utf-8").splitlines()[1]

        assert header == "# Generated by pcons from caller.py. Do not edit."

    def test_the_payload_carries_the_protocol_and_the_kwargs(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        _, args_rel = run_emit(project, env, takes_arguments, kwargs={"n": 3})
        payload = pickle.loads((tmp_path / args_rel).read_bytes())

        assert payload == {
            "version": PROTOCOL_VERSION,
            "module": f"{MODULE_PREFIX}takes_arguments",
            "function": "takes_arguments",
            "kwargs": {"n": 3},
            "path": None,
        }

    def test_what_it_writes_is_what_the_runner_runs(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, writes_sources)
        target = tmp_path / "out.txt"

        run(str(tmp_path / module_rel), str(tmp_path / args_rel), [str(target)], ["a"])

        assert target.read_text() == "a"

    def test_a_target_in_a_subdirectory_nests_the_pickle(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(
            project, env, writes_sources, target="out/report.txt"
        )

        assert args_rel == Path("build/pybuilder/out/report.txt.args.pkl")
        assert module_rel == Path("build/pybuilder/writes_sources.py")
        assert (tmp_path / args_rel).is_file()

    def test_a_target_outside_the_build_directory_falls_back_to_the_name(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The pickle stays inside the generated directory either way.

        An external target's own node path is absolute, and cannot be
        expressed relative to the gen dir, so the fallback is the sanitized
        edge name, today's behaviour, rather than reasoning further about
        where the pickle should land.
        """
        outside = str(tmp_path.parent / "outside.txt")
        module_rel, args_rel = run_emit(
            project, env, writes_sources, name="a/b", target=outside
        )

        assert args_rel == Path("build/pybuilder/a_b.args.pkl")
        assert module_rel == Path("build/pybuilder/writes_sources.py")
        assert (tmp_path / args_rel).is_file()

    def test_a_sub_project_writes_under_its_own_slice(
        self, project: Project, tmp_path: Path
    ) -> None:
        (tmp_path / "sub").mkdir()
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            child_env = child.Environment()
            module_rel, _ = run_emit(child, child_env, writes_sources)

        assert module_rel == Path("build/sub/pybuilder/writes_sources.py")
        assert (tmp_path / module_rel).is_file()

    def test_two_subdirectories_may_share_one_environment(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The legal half of the duplicate rule: same name, two slices."""
        for name in ("a", "b"):
            (tmp_path / name).mkdir()

        with project._enter_subdir("a"):
            first, _ = run_emit(project, env, writes_sources)
        with project._enter_subdir("b"):
            second, _ = run_emit(project, env, writes_sources)

        assert first == Path("build/a/pybuilder/writes_sources.py")
        assert second == Path("build/b/pybuilder/writes_sources.py")

    def test_a_build_prefix_moves_both_files(
        self, project: Project, tmp_path: Path
    ) -> None:
        env = project.Environment(name="host")
        env.build_prefix = "host"

        module_rel, args_rel = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/host/pybuilder/writes_sources.py")
        assert args_rel == Path("build/host/pybuilder/report.args.pkl")


class TestDuplicates:
    def test_a_second_decoration_of_one_function_is_refused(
        self, project: Project, env: Any
    ) -> None:
        """Two decorations are two functions, and a module has one owner."""
        run_emit(project, env, writes_sources)

        with pytest.raises(PyBuilderError) as caught:
            run_emit(project, env, writes_sources)

        message = str(caught.value)
        assert "would overwrite build/pybuilder/writes_sources.py" in message
        assert "Decorate the function once and call the builder twice." in message

    def test_two_functions_of_one_name_are_refused_naming_both(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        first = write_module(
            tmp_path,
            "first_render",
            """
            def render(targets, sources):
                return 1
            """,
        )
        second = write_module(
            tmp_path,
            "second_render",
            """
            def render(targets, sources):
                return 2
            """,
        )
        run_emit(project, env, first.render, name="a")

        with pytest.raises(PyBuilderError) as caught:
            run_emit(project, env, second.render, name="b")

        message = str(caught.value)
        assert "would overwrite build/pybuilder/render.py" in message
        assert "test_pybuilder_emit.py:" in message.split("already written by")[1]
        assert "Rename one of the functions." in message
        assert "name=" not in message

    def test_a_second_edge_of_one_name_collides_on_the_pickle(
        self, project: Project, env: Any
    ) -> None:
        emit_args(project=project, env=env, name="report", target="report")

        with pytest.raises(PyBuilderError, match=r"report\.args\.pkl"):
            emit_args(project=project, env=env, name="report", target="report")

    def test_the_same_name_in_two_environments_is_fine(
        self, project: Project, env: Any
    ) -> None:
        other = project.Environment(name="host")
        other.build_prefix = "host"

        first, _ = run_emit(project, env, writes_sources)
        second, _ = run_emit(project, other, writes_sources)

        assert first != second

    def test_two_environments_sharing_a_build_directory_are_refused(
        self, project: Project, env: Any
    ) -> None:
        other = project.Environment(name="twin")

        run_emit(project, env, writes_sources)

        with pytest.raises(PyBuilderError, match="would overwrite"):
            run_emit(project, other, writes_sources)

    def test_each_project_starts_with_a_clean_registry(self, tmp_path: Path) -> None:
        first = Project("first", root_dir=tmp_path)
        run_emit(first, first.Environment(), writes_sources)

        Project._clear_tree()
        second = Project("second", root_dir=tmp_path)
        module_rel, _ = run_emit(second, second.Environment(), writes_sources)

        assert (tmp_path / module_rel).is_file()


class TestOneModuleManyEdges:
    def test_two_edges_of_one_function_share_one_module(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        function = validate(takes_arguments, project=project)

        module_rel, module_bytes = emit_module(function, project=project, env=env)
        again, no_bytes = emit_module(function, project=project, env=env)
        first = emit_args(project=project, env=env, name="one", target="one")
        second = emit_args(project=project, env=env, name="two", target="two")

        assert module_bytes is not None
        assert (again, no_bytes) == (module_rel, None)
        assert first != second
        assert first.parent == second.parent == module_rel.parent

    def test_the_module_holds_the_function_text(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        function = validate(writes_sources, project=project)
        _, module_bytes = emit_module(function, project=project, env=env)

        assert module_bytes is not None
        assert module_bytes.decode("utf-8") == function.module_text

    def test_a_second_emit_for_one_environment_has_nothing_to_write(
        self, project: Project, env: Any
    ) -> None:
        """The claim is what drops the second write, not the write-if-changed test."""
        function = validate(writes_sources, project=project)
        module_rel, _ = emit_module(function, project=project, env=env)

        assert emit_module(function, project=project, env=env) == (module_rel, None)

    def test_two_environments_sharing_a_directory_share_the_module(
        self, project: Project, env: Any
    ) -> None:
        """What the owner is for: one file, two edges, no refusal."""
        twin = project.Environment(name="twin")
        function = validate(writes_sources, project=project)

        first, first_bytes = emit_module(function, project=project, env=env)
        second, second_bytes = emit_module(function, project=project, env=twin)

        assert first == second
        assert first_bytes is not None
        assert second_bytes is None


class TestNothingIsWrittenUntilEverythingIsChecked:
    def test_a_refused_argument_leaves_no_module_and_no_claim(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """The arguments are settled before the module reaches the disk."""
        function = validate(takes_arguments, project=project)

        with pytest.raises(PyBuilderError, match="cannot pickle"):
            check_arguments(function, kwargs={"handle": lambda: None}, sys_path=None)

        assert not (tmp_path / "build" / "pybuilder").exists()

        _, module_bytes = emit_module(function, project=project, env=env)

        assert module_bytes is not None


class TestValidatedFunctionIdentity:
    def test_two_validations_of_one_def_are_two_functions(
        self, project: Project, tmp_path: Path
    ) -> None:
        """The factory idiom, which a generated __eq__ would call one function."""

        def decorate() -> ValidatedFunction:
            def render(targets, sources):
                return 1

            return validate(render, project=project)

        first = decorate()
        second = decorate()

        assert first is not second
        assert first != second
        assert len({first, second}) == 2
        assert first.module_text == second.module_text


class TestReservedNames:
    """The reserved set is the call's own keyword-only parameters.

    The two drifted apart once already, which is why this is derived rather
    than listed. If deriving is ever replaced by a literal, this fails.
    """

    def test_it_is_exactly_what_the_call_spends_on_the_edge(self) -> None:
        call = inspect.signature(PyBuilder.__call__).parameters

        assert _reserved_names() == {
            name
            for name, parameter in call.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        }

    def test_it_holds_the_four_names_the_call_names_today(self) -> None:
        assert _reserved_names() == {"target", "source", "name", "depends"}

    def test_it_excludes_self_and_the_functions_own_arguments(self) -> None:
        assert "self" not in _reserved_names()
        assert "kwargs" not in _reserved_names()


class TestClaimRegistry:
    """The owner rule on its own, without a function or a file in the way."""

    def _at(self, lineno: int) -> SourceLocation:
        return SourceLocation("pcons-build.py", lineno, "build")

    def _function(self, project: Project) -> ValidatedFunction:
        return validate(writes_sources, project=project)

    def _other_function(self, project: Project) -> ValidatedFunction:
        """A second function whose module text differs from ``_function``'s."""
        return validate(annotated, project=project)

    def test_a_first_claim_says_to_write(self, project: Project, env: Any) -> None:
        claimed = _claim(
            project,
            env,
            Path("build/pybuilder/x.py"),
            "x",
            self._at(1),
            owner=self._function(project),
        )

        assert claimed is True

    def test_the_same_owner_again_says_not_to_write(
        self, project: Project, env: Any
    ) -> None:
        owner = self._function(project)
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=owner)

        assert _claim(project, env, path, "x", self._at(2), owner=owner) is False

    def test_another_function_on_one_path_says_to_rename(
        self, project: Project, env: Any
    ) -> None:
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=self._function(project))

        with pytest.raises(PyBuilderError, match="Rename one of the functions"):
            _claim(
                project,
                env,
                path,
                "x",
                self._at(2),
                owner=self._other_function(project),
            )

    def test_the_same_function_twice_says_to_call_the_builder_twice(
        self, project: Project, env: Any
    ) -> None:
        """Two decorations of one function, which the module text tells apart."""
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=self._function(project))

        with pytest.raises(PyBuilderError) as caught:
            _claim(project, env, path, "x", self._at(2), owner=self._function(project))

        message = str(caught.value)
        assert "Decorate the function once and call the builder twice." in message
        assert "Rename" not in message

    def test_no_owner_on_a_taken_path_is_refused(
        self, project: Project, env: Any
    ) -> None:
        """A pickle's path is exclusive, even against the module's owner."""
        path = Path("build/pybuilder/x.args.pkl")
        owner = self._function(project)
        _claim(project, env, path, "x", self._at(1), owner=owner)

        with pytest.raises(PyBuilderError) as caught:
            _claim(project, env, path, "x", self._at(2), owner=None)

        message = str(caught.value)
        assert "PyBuilder edge 'x' would overwrite" in message
        assert "Give the edges different targets." in message

    def test_a_second_claim_with_no_owner_at_all_is_refused(
        self, project: Project, env: Any
    ) -> None:
        path = Path("build/pybuilder/x.args.pkl")
        _claim(project, env, path, "x", self._at(1), owner=None)

        with pytest.raises(PyBuilderError, match=r"Give the edges different targets"):
            _claim(project, env, path, "x", self._at(2), owner=None)

    def test_a_shared_owner_across_environments_still_shares(
        self, project: Project, env: Any
    ) -> None:
        owner = self._function(project)
        twin = project.Environment(name="twin")
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=owner)

        assert _claim(project, twin, path, "x", self._at(2), owner=owner) is False

    def test_one_function_across_environments_names_build_prefix_alone(
        self, project: Project, env: Any
    ) -> None:
        """One def in a factory: there is no second function to rename."""
        twin = project.Environment(name="twin")
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=self._function(project))

        with pytest.raises(PyBuilderError) as caught:
            _claim(project, twin, path, "x", self._at(2), owner=self._function(project))

        message = str(caught.value)
        assert "Give one environment its own build_prefix." in message
        assert "rename" not in message
        assert "name=" not in message

    def test_another_owner_across_environments_names_build_prefix(
        self, project: Project, env: Any
    ) -> None:
        twin = project.Environment(name="twin")
        path = Path("build/pybuilder/x.py")
        _claim(project, env, path, "x", self._at(1), owner=self._function(project))

        with pytest.raises(PyBuilderError) as caught:
            _claim(
                project,
                twin,
                path,
                "x",
                self._at(2),
                owner=self._other_function(project),
            )

        message = str(caught.value)
        assert "Give one environment its own build_prefix" in message
        assert "rename one of the functions" in message


class TestWriteIfChanged:
    def _emit_again(
        self, tmp_path: Path, fn: Any, kwargs: dict[str, Any]
    ) -> tuple[Path, Path]:
        """A second run of pcons over the same tree: a fresh Project.

        The tree reset is what a new process gives for free, and it is what
        releases the default build directory for the second project.
        """
        Project._clear_tree()
        project = Project("again", root_dir=tmp_path)
        return run_emit(project, project.Environment(), fn, kwargs=kwargs)

    def _aged(self, tmp_path: Path, *paths: Path) -> None:
        for path in paths:
            os.utime(tmp_path / path, (0, 0))

    def test_an_unchanged_description_touches_neither_file(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, takes_arguments, kwargs={"n": 1})
        self._aged(tmp_path, module_rel, args_rel)

        self._emit_again(tmp_path, takes_arguments, {"n": 1})

        assert (tmp_path / module_rel).stat().st_mtime == 0
        assert (tmp_path / args_rel).stat().st_mtime == 0

    def test_a_changed_body_rewrites_only_the_module(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        first = write_module(
            tmp_path,
            "body_one",
            """
            def render(targets, sources, n):
                return 1
            """,
        )
        second = write_module(
            tmp_path,
            "body_two",
            """
            def render(targets, sources, n):
                return 2
            """,
        )
        module_rel, args_rel = run_emit(project, env, first.render, kwargs={"n": 1})
        self._aged(tmp_path, module_rel, args_rel)

        self._emit_again(tmp_path, second.render, {"n": 1})

        assert (tmp_path / module_rel).stat().st_mtime != 0
        assert (tmp_path / args_rel).stat().st_mtime == 0

    def test_changed_kwargs_rewrite_only_the_pickle(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, takes_arguments, kwargs={"n": 1})
        self._aged(tmp_path, module_rel, args_rel)

        self._emit_again(tmp_path, takes_arguments, {"n": 2})

        assert (tmp_path / module_rel).stat().st_mtime == 0
        assert (tmp_path / args_rel).stat().st_mtime != 0
