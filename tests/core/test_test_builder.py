# SPDX-License-Identifier: MIT
"""Tests for the Test builder, TestSpec, and manifest writer.

These tests exercise the *configuration* side of the test feature:
target creation, dependency wiring, spec finalization during resolve,
and JSON manifest generation. The runner is tested separately in
``tests/test_runner.py``.
"""

from __future__ import annotations

import json

import pytest

from pcons.core.project import Project
from pcons.core.target import Target
from pcons.core.test import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    TestSpec,
    collect_test_specs,
    set_test_properties,
    set_test_property,
    write_test_manifest,
)


@pytest.fixture
def project(tmp_path, gcc_toolchain):
    """A minimal project with a tiny C program for tests to reference."""
    src = tmp_path / "main.c"
    src.write_text("int main(void){return 0;}\n")
    proj = Project("unit", root_dir=tmp_path, build_dir=tmp_path / "build")
    env = proj.Environment(toolchain=gcc_toolchain)
    prog = proj.Program("prog", env, sources=[str(src)])
    return proj, env, prog


class TestBuilderTargetCreation:
    """The Test builder creates a properly-shaped Target."""

    def test_basic_target_attributes(self, project):
        proj, _env, prog = project
        t = proj.Test("hello.smoke", prog)

        assert t.target_type == "test"
        assert t._builder_name == "Test"
        assert t.name.startswith("test_")
        # Depends on the program so test-build / topo sort work.
        assert prog in t.dependencies
        # No output files: tests are purely declarative.
        assert t.output_nodes == []

    def test_partial_spec_captured_before_resolve(self, project):
        proj, _env, prog = project
        t = proj.Test(
            "hello.smoke",
            prog,
            args=["--quick"],
            labels=["unit", "fast"],
            timeout=5.0,
            should_fail=True,
            serial=True,
            disabled=True,
        )

        partial = t._builder_data["spec_partial"]
        assert partial["name"] == "hello.smoke"
        assert partial["args"] == ["--quick"]
        assert partial["labels"] == ("unit", "fast")
        assert partial["timeout"] == 5.0
        assert partial["should_fail"] is True
        assert partial["serial"] is True
        assert partial["disabled"] is True

    def test_name_collision_is_disambiguated(self, project):
        """Two tests with the same name produce two unique target names."""
        proj, _env, prog = project
        a = proj.Test("dup", prog)
        b = proj.Test("dup", prog)
        assert a.name != b.name
        # User-visible spec name should match what the user supplied,
        # disambiguation happens only at the internal target-name level.
        assert a._builder_data["spec_partial"]["name"] == "dup"
        assert b._builder_data["spec_partial"]["name"] == "dup"

    def test_rejects_empty_name(self, project):
        proj, _env, prog = project
        with pytest.raises(TypeError, match="non-empty"):
            proj.Test("", prog)

    def test_friendly_names_sanitized_internally(self, project):
        """Spaces, colons, parens etc. in user-facing names don't crash.

        Catch2 test names ("first scenario") and gtest names with
        special chars would otherwise fail Target._validate_target_name.
        The user-visible name on the spec is preserved unchanged.
        """
        proj, _env, prog = project
        cases = [
            "addition works",
            "NetSuite::ssl handshake",
            "edge case (n=0)",
            "regex /foo\\d+/",
        ]
        targets = [proj.Test(name, prog) for name in cases]
        proj.resolve()
        for name, target in zip(cases, targets, strict=True):
            # Internal target name is Ninja-safe...
            assert " " not in target.name
            assert ":" not in target.name
            # ...but the user-visible name on the spec is unchanged.
            assert target._builder_data["spec"].name == name


class TestSpecFinalization:
    """Resolution turns the partial spec into a fully-resolved TestSpec."""

    def test_spec_built_during_resolve(self, project):
        proj, _env, prog = project
        t = proj.Test("hello", prog, args=["a", "b"], labels=["unit"])
        proj.resolve()

        spec = t._builder_data.get("spec")
        assert isinstance(spec, TestSpec)
        assert spec.name == "hello"
        assert spec.command[1:] == ["a", "b"]
        assert spec.labels == ("unit",)
        # Partial is dropped after finalization.
        assert "spec_partial" not in t._builder_data

    def test_program_path_is_build_dir_relative(self, project):
        proj, _env, prog = project
        t = proj.Test("hello", prog)
        proj.resolve()

        spec = t._builder_data["spec"]
        # First command element is the program path. After resolution it
        # should not contain a leading "build/" prefix or be absolute —
        # the runner anchors paths to the build directory.
        program_arg = spec.command[0]
        assert not program_arg.startswith("/")
        assert not program_arg.startswith("build/")


class TestManifestSerialization:
    """write_test_manifest produces a valid, complete JSON manifest."""

    def test_no_manifest_when_no_tests(self, tmp_path):
        proj = Project("empty", root_dir=tmp_path, build_dir=tmp_path / "build")
        result = write_test_manifest(proj, tmp_path / "build")
        assert result is None
        assert not (tmp_path / "build" / MANIFEST_FILENAME).exists()

    def test_manifest_schema_header(self, project, tmp_path):
        proj, _env, prog = project
        proj.Test("a", prog)
        proj.resolve()
        out = tmp_path / "build"
        out.mkdir(exist_ok=True)
        path = write_test_manifest(proj, out)

        assert path is not None
        data = json.loads(path.read_text())
        assert data["version"] == MANIFEST_VERSION
        assert data["project"] == "unit"
        assert "tests" in data
        assert isinstance(data["tests"], list)

    def test_manifest_records_every_test(self, project, tmp_path):
        proj, _env, prog = project
        proj.Test("a", prog, labels=["unit"])
        proj.Test("b", prog, labels=["integration"])
        proj.Test("c", prog, disabled=True)
        proj.resolve()
        out = tmp_path / "build"
        out.mkdir(exist_ok=True)
        path = write_test_manifest(proj, out)

        data = json.loads(path.read_text())
        names = [t["name"] for t in data["tests"]]
        assert names == ["a", "b", "c"]
        assert data["tests"][2]["disabled"] is True

    def test_spec_jsonable_roundtrip(self):
        """TestSpec.to_jsonable() produces a dict that round-trips through JSON."""
        spec = TestSpec(
            name="x",
            command=["./x"],
            cwd=None,
            env={"K": "V"},
            labels=("unit",),
            timeout=2.0,
            should_fail=False,
            serial=False,
            disabled=False,
            data=(),
            defined_at="x.py:1",
        )
        as_json = json.dumps(spec.to_jsonable())
        back = json.loads(as_json)
        assert back["name"] == "x"
        assert back["env"] == {"K": "V"}
        assert back["labels"] == ["unit"]


class TestDependsOn:
    """The depends_on field threads through builder, partial spec, and TestSpec."""

    def test_field_set_via_builder(self, project):
        proj, _env, prog = project
        proj.Test("a", prog)
        b = proj.Test("b", prog, depends_on=["a"])
        proj.resolve()
        assert b._builder_data["spec"].depends_on == ("a",)

    def test_appears_in_manifest(self, project, tmp_path):
        proj, _env, prog = project
        proj.Test("setup", prog)
        proj.Test("uses_setup", prog, depends_on=["setup"])
        proj.resolve()
        out = tmp_path / "build"
        out.mkdir(exist_ok=True)
        path = write_test_manifest(proj, out)
        data = json.loads(path.read_text())
        # Look up by name; manifest order isn't load-bearing here.
        by_name = {t["name"]: t for t in data["tests"]}
        assert by_name["uses_setup"]["depends_on"] == ["setup"]
        assert by_name["setup"]["depends_on"] == []


class TestDiscoverField:
    """The discover field is validated and propagated to the spec."""

    def test_valid_discover_values(self, project):
        proj, _env, prog = project
        for proto in ("gtest", "doctest", "catch2"):
            t = proj.Test(f"t_{proto}", prog, discover=proto)
            assert t._builder_data["spec_partial"]["discover"] == proto

    def test_unknown_discover_raises(self, project):
        proj, _env, prog = project
        with pytest.raises(ValueError, match="not a known protocol"):
            proj.Test("bogus", prog, discover="pytest")

    def test_discover_serializes(self, project, tmp_path):
        proj, _env, prog = project
        proj.Test("u", prog, discover="doctest")
        proj.resolve()
        out = tmp_path / "build"
        out.mkdir(exist_ok=True)
        path = write_test_manifest(proj, out)
        data = json.loads(path.read_text())
        assert data["tests"][0]["discover"] == "doctest"


class TestSetTestProperty:
    """set_test_property mutates an unresolved Test target's spec_partial."""

    def test_update_single_property(self, project):
        proj, _env, prog = project
        t = proj.Test("x", prog)
        set_test_property(t, "timeout", 30.0)
        proj.resolve()
        assert t._builder_data["spec"].timeout == 30.0

    def test_update_multiple_properties(self, project):
        proj, _env, prog = project
        t = proj.Test("x", prog)
        set_test_properties(t, timeout=12.0, labels=["unit", "slow"], disabled=True)
        proj.resolve()
        spec = t._builder_data["spec"]
        assert spec.timeout == 12.0
        assert spec.labels == ("unit", "slow")
        assert spec.disabled is True

    def test_coerces_collection_types(self, project):
        proj, _env, prog = project
        t = proj.Test("x", prog)
        set_test_property(t, "labels", ["a", "b"])
        set_test_property(t, "depends_on", ["other"])
        # Labels stored as tuple, depends_on as tuple in the partial dict
        partial = t._builder_data["spec_partial"]
        assert partial["labels"] == ("a", "b")
        assert partial["depends_on"] == ("other",)

    def test_rejects_unknown_key(self, project):
        proj, _env, prog = project
        t = proj.Test("x", prog)
        with pytest.raises(KeyError, match="Unknown test property"):
            set_test_property(t, "frobnicate", 5)

    def test_rejects_non_test_target(self, project):
        proj, _env, prog = project
        with pytest.raises(TypeError, match="not a Test target"):
            set_test_property(prog, "timeout", 30)

    def test_rejects_after_resolve(self, project):
        proj, _env, prog = project
        t = proj.Test("x", prog)
        proj.resolve()
        with pytest.raises(RuntimeError, match="after resolve"):
            set_test_property(t, "timeout", 30)

    def test_set_test_properties_bulk(self, project):
        proj, _env, prog = project
        t1 = proj.Test("a", prog)
        t2 = proj.Test("b", prog)
        set_test_properties(t1, t2, timeout=99.0, labels=["bulk"])
        proj.resolve()
        assert t1._builder_data["spec"].timeout == 99.0
        assert t2._builder_data["spec"].timeout == 99.0
        assert t1._builder_data["spec"].labels == ("bulk",)


class TestCollectTestSpecs:
    """collect_test_specs only returns finalized TestSpec instances."""

    def test_collects_in_definition_order(self, project):
        proj, _env, prog = project
        proj.Test("first", prog)
        proj.Test("second", prog)
        proj.Test("third", prog)
        proj.resolve()

        specs = collect_test_specs(proj)
        assert [s.name for s in specs] == ["first", "second", "third"]

    def test_ignores_non_test_targets(self, project):
        proj, _env, _prog = project
        proj.Test("the_only_one", _prog)
        proj.resolve()

        specs = collect_test_specs(proj)
        assert len(specs) == 1
        # The program target is also in proj.targets but not a Test.
        non_tests = [t for t in proj.targets if t.target_type != "test"]
        assert all(isinstance(t, Target) for t in non_tests)
