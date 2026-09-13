# SPDX-License-Identifier: MIT
"""Tests for the generate-time half of PyAction, pcons.tools.pyaction."""

from __future__ import annotations

import functools
import importlib.util
import os
import pickle
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pcons.core.project import Project
from pcons.tools.pyaction import (
    MODULE_PREFIX,
    PyActionError,
    emit,
    function_source,
)
from pcons.util.pyaction import PROTOCOL_VERSION, run

SCRIPT_GLOBAL = "visible from the build script only"


def keep(*args: object, **kwargs: object):
    """A decorator that returns the function untouched."""

    def wrap(fn):
        return fn

    return wrap


@keep()
def decorated(sources, targets):
    return len(sources) + len(targets)


def documented(sources, targets):
    """A docstring."""

    class Inner:
        value = 1

    def nested():
        return Inner.value

    return nested()


if True:

    def indented(sources, targets):
        return "indented"


def factory(unused):
    @keep()
    def inner(sources, targets):
        return "from a factory"

    return inner


def closing_over(env):
    def inner(sources, targets):
        return env

    return inner


class Holder:
    def method(self, sources, targets):
        return 1


async def coroutine(sources, targets):
    return 1


def uses_a_script_global(sources, targets):
    return SCRIPT_GLOBAL


def writes_sources(sources, targets):
    with open(targets[0], "w") as out:
        out.write("|".join(sources))


def defaults_from_the_script(sources, targets, label=SCRIPT_GLOBAL):
    return label


def annotated(sources, targets, out: Path | None = None) -> Path | None:
    return out


def uses_dunder_file(sources, targets):
    return __file__


def run_emit(
    project: Project,
    env: Any,
    fn: Any,
    name: str = "report",
    kwargs: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Every emit in this file goes through here, on one line.

    ``emit`` records the caller's location in the generated module's header,
    so calls from two different lines would differ in content for that reason
    alone and no mtime test could say anything.
    """
    return emit(fn, project=project, env=env, name=name, kwargs=kwargs or {})


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


def plain(sources, targets):
    return 1


@keep()
def once(sources, targets):
    return 1


@keep()
@keep("a")
def twice(sources, targets):
    return 1


@keep(
    "a",
    "b",
)
def multiline(sources, targets):
    return 1


# A comment above the decoration.
@keep()
def commented(sources, targets):
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
        with pytest.raises(PyActionError, match="coroutine"):
            function_source(coroutine)

    def test_a_function_with_no_readable_source_is_refused(self) -> None:
        namespace: dict[str, Any] = {}
        exec("def render(sources, targets):\n    return 1\n", namespace)  # noqa: S102

        with pytest.raises(PyActionError, match="written out in a build script"):
            function_source(namespace["render"])


class TestRejections:
    def test_a_lambda(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="lambda"):
            run_emit(project, env, lambda sources, targets: None)

    def test_a_closure_names_its_free_variables(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyActionError, match="reads env from"):
            run_emit(project, env, closing_over(env))

    def test_a_builtin(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="written in a build script"):
            run_emit(project, env, len)

    def test_a_partial(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="functools.partial"):
            run_emit(project, env, functools.partial(writes_sources, []))

    def test_a_method(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="class body"):
            run_emit(project, env, Holder.method)

    def test_a_bound_method(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="written in a build script"):
            run_emit(project, env, Holder().method)

    def test_a_body_reading_a_script_global(self, project: Project, env: Any) -> None:
        with pytest.raises(PyActionError, match="SCRIPT_GLOBAL"):
            run_emit(project, env, uses_a_script_global)

    def test_an_attribute_name_is_not_mistaken_for_a_global(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module = write_module(
            tmp_path,
            "attrs",
            """
            load = "a name this module also defines"


            def render(sources, targets):
                with open(targets[0], "w") as out:
                    out.load = 1
                    out.write("ok")
            """,
        )

        run_emit(project, env, module.render)

    def test_a_coroutine_reaches_the_error_through_emit(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyActionError, match="coroutine"):
            run_emit(project, env, coroutine)

    def test_a_refused_function_does_not_claim_its_module(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyActionError, match="coroutine"):
            run_emit(project, env, coroutine)

        module_rel, _ = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/pyact/report.py")

    def test_a_default_reading_a_script_global(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyActionError, match="SCRIPT_GLOBAL"):
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
        with pytest.raises(PyActionError, match="names the generated module"):
            run_emit(project, env, uses_dunder_file)

    def test_an_unpicklable_kwarg_names_the_key(
        self, project: Project, env: Any
    ) -> None:
        with pytest.raises(PyActionError, match="cannot pickle kwargs handle"):
            run_emit(
                project,
                env,
                writes_sources,
                kwargs={"n": 1, "handle": lambda: None},
            )


class TestEmit:
    def test_the_returned_paths_are_build_relative(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/pyact/report.py")
        assert args_rel == Path("build/pyact/report.args.pkl")
        assert (tmp_path / module_rel).is_file()
        assert (tmp_path / args_rel).is_file()

    def test_the_module_holds_the_function_and_its_origin(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, _ = run_emit(project, env, writes_sources)
        text = (tmp_path / module_rel).read_text(encoding="utf-8")

        assert text.startswith("# SPDX-License-Identifier: MIT\n")
        assert text.splitlines()[1].endswith("from test_pyaction_emit.py. Do not edit.")
        assert "from __future__ import annotations" in text
        assert "def writes_sources(sources, targets):" in text
        assert "@" not in text

    def test_the_origin_carries_no_line_number(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        """A line inserted above the decoration must not rewrite the module.

        The two emits below sit on different lines of this file, which is
        what a line inserted above one of them would do to the other.
        """
        module_rel, _ = emit(
            writes_sources, project=project, env=env, name="report", kwargs={}
        )
        os.utime(tmp_path / module_rel, (0, 0))

        Project._clear_tree()
        again = Project("again", root_dir=tmp_path)
        emit(
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
            from pcons.tools.pyaction import emit


            def render(sources, targets):
                return 1


            def emit_it(project, env):
                return emit(render, project=project, env=env,
                            name="report", kwargs={})
            """,
        )

        module_rel, _ = caller.emit_it(project, env)
        header = (tmp_path / module_rel).read_text(encoding="utf-8").splitlines()[1]

        assert header == "# Generated by pcons from caller.py. Do not edit."

    def test_the_payload_carries_the_protocol_and_the_kwargs(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        _, args_rel = run_emit(project, env, writes_sources, kwargs={"n": 3})
        payload = pickle.loads((tmp_path / args_rel).read_bytes())

        assert payload == {
            "version": PROTOCOL_VERSION,
            "module": f"{MODULE_PREFIX}report",
            "function": "writes_sources",
            "kwargs": {"n": 3},
        }

    def test_what_it_writes_is_what_the_runner_runs(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, writes_sources)
        target = tmp_path / "out.txt"

        run(str(tmp_path / module_rel), str(tmp_path / args_rel), [str(target)], ["a"])

        assert target.read_text() == "a"

    def test_a_name_with_a_slash_stays_inside_the_generated_directory(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        module_rel, args_rel = run_emit(project, env, writes_sources, name="a/b.txt")

        assert module_rel == Path("build/pyact/a_b_txt.py")
        assert args_rel.parent == Path("build/pyact")
        assert (tmp_path / module_rel).is_file()

    def test_a_sub_project_writes_under_its_own_slice(
        self, project: Project, tmp_path: Path
    ) -> None:
        (tmp_path / "sub").mkdir()
        with project._enter_subdir("sub"):
            child = Project("child", root_dir=tmp_path / "sub")
            child_env = child.Environment()
            module_rel, _ = run_emit(child, child_env, writes_sources)

        assert module_rel == Path("build/sub/pyact/report.py")
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

        assert first == Path("build/a/pyact/report.py")
        assert second == Path("build/b/pyact/report.py")

    def test_a_build_prefix_moves_both_files(
        self, project: Project, tmp_path: Path
    ) -> None:
        env = project.Environment(name="host")
        env.build_prefix = "host"

        module_rel, args_rel = run_emit(project, env, writes_sources)

        assert module_rel == Path("build/host/pyact/report.py")
        assert args_rel == Path("build/host/pyact/report.args.pkl")


class TestDuplicates:
    def test_the_same_name_twice_in_one_environment_is_refused(
        self, project: Project, env: Any
    ) -> None:
        run_emit(project, env, writes_sources)

        with pytest.raises(PyActionError, match="would overwrite"):
            run_emit(project, env, writes_sources)

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

        with pytest.raises(PyActionError, match="would overwrite"):
            run_emit(project, other, writes_sources)

    def test_each_project_starts_with_a_clean_registry(self, tmp_path: Path) -> None:
        first = Project("first", root_dir=tmp_path)
        run_emit(first, first.Environment(), writes_sources)

        Project._clear_tree()
        second = Project("second", root_dir=tmp_path)
        module_rel, _ = run_emit(second, second.Environment(), writes_sources)

        assert (tmp_path / module_rel).is_file()


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
        module_rel, args_rel = run_emit(project, env, writes_sources, kwargs={"n": 1})
        self._aged(tmp_path, module_rel, args_rel)

        self._emit_again(tmp_path, writes_sources, {"n": 1})

        assert (tmp_path / module_rel).stat().st_mtime == 0
        assert (tmp_path / args_rel).stat().st_mtime == 0

    def test_a_changed_body_rewrites_only_the_module(
        self, project: Project, env: Any, tmp_path: Path
    ) -> None:
        first = write_module(
            tmp_path,
            "body_one",
            """
            def render(sources, targets):
                return 1
            """,
        )
        second = write_module(
            tmp_path,
            "body_two",
            """
            def render(sources, targets):
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
        module_rel, args_rel = run_emit(project, env, writes_sources, kwargs={"n": 1})
        self._aged(tmp_path, module_rel, args_rel)

        self._emit_again(tmp_path, writes_sources, {"n": 2})

        assert (tmp_path / module_rel).stat().st_mtime == 0
        assert (tmp_path / args_rel).stat().st_mtime != 0
