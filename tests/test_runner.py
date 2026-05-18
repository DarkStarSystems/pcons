# SPDX-License-Identifier: MIT
"""Tests for the standalone test runner (pcons/test_runner.py).

These exercise the parts of the runner that don't depend on a real
build: manifest filtering, single-test execution with a tiny shell
program, timeout / should_fail / disabled handling, parallelism, and
JUnit XML output. The CLI entry point is also exercised via
``main([...])`` so the full path from argv to exit code is covered.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pcons.test_runner import (
    ERROR,
    FAIL,
    PASS,
    SKIP,
    TIMEOUT,
    TestResult,
    _discover_catch2,
    _discover_doctest,
    _discover_gtest,
    _validate_deps,
    expand_discovered_tests,
    expand_filter_with_deps,
    filter_tests,
    find_manifest,
    load_manifest,
    main,
    run_all,
    run_one_test,
    write_junit,
)


def _make_exit_script(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable Python script that the runner can subprocess."""
    p = tmp_path / name
    p.write_text(f"#!{sys.executable}\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _make_manifest(tmp_path: Path, tests: list[dict]) -> Path:
    manifest = tmp_path / "tests.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "project": "rt_test",
                "build_dir": str(tmp_path),
                "tests": tests,
            }
        )
    )
    return manifest


# ----- filter_tests --------------------------------------------------------


class TestFiltering:
    def test_label_include(self):
        tests = [
            {"name": "a", "labels": ["unit", "fast"]},
            {"name": "b", "labels": ["integration"]},
        ]
        kept = filter_tests(
            tests,
            include_labels=["unit"],
            exclude_labels=[],
            include_regex=None,
            exclude_regex=None,
        )
        assert [t["name"] for t in kept] == ["a"]

    def test_label_exclude(self):
        tests = [
            {"name": "a", "labels": ["unit"]},
            {"name": "b", "labels": ["slow"]},
        ]
        kept = filter_tests(
            tests,
            include_labels=[],
            exclude_labels=["slow"],
            include_regex=None,
            exclude_regex=None,
        )
        assert [t["name"] for t in kept] == ["a"]

    def test_name_regex_include(self):
        tests = [
            {"name": "math.add", "labels": []},
            {"name": "math.mul", "labels": []},
            {"name": "string.len", "labels": []},
        ]
        kept = filter_tests(
            tests,
            include_labels=[],
            exclude_labels=[],
            include_regex=r"^math\.",
            exclude_regex=None,
        )
        assert [t["name"] for t in kept] == ["math.add", "math.mul"]

    def test_combined_filters_and_substring_match(self):
        tests = [
            {"name": "x.unit", "labels": ["xfail_unit"]},
            {"name": "y.unit", "labels": ["regression"]},
        ]
        # -L "unit" is a substring match against the labels.
        kept = filter_tests(
            tests,
            include_labels=["unit"],
            exclude_labels=[],
            include_regex=None,
            exclude_regex=r"^y\.",
        )
        assert [t["name"] for t in kept] == ["x.unit"]


# ----- find_manifest / load_manifest --------------------------------------


class TestManifestDiscovery:
    def test_finds_manifest_in_cwd(self, tmp_path):
        m = _make_manifest(tmp_path, [])
        found = find_manifest(tmp_path)
        assert found == m

    def test_finds_manifest_in_build_subdir(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        m = _make_manifest(build, [])
        found = find_manifest(tmp_path)
        assert found == m

    def test_walks_upward(self, tmp_path):
        # manifest at <tmp>/build/tests.json, search from <tmp>/src/sub/
        build = tmp_path / "build"
        build.mkdir()
        m = _make_manifest(build, [])
        nested = tmp_path / "src" / "sub"
        nested.mkdir(parents=True)
        found = find_manifest(nested)
        assert found == m

    def test_load_manifest_validates_shape(self, tmp_path):
        bad = tmp_path / "tests.json"
        bad.write_text(json.dumps({"version": 1, "tests": "not a list"}))
        with pytest.raises(ValueError, match="must be a list"):
            load_manifest(bad)


# ----- run_one_test --------------------------------------------------------


class TestSingleTestExecution:
    def test_pass(self, tmp_path):
        script = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        result = run_one_test(
            {"name": "ok", "command": [str(script)], "labels": []},
            tmp_path,
        )
        assert result.status == PASS
        assert result.returncode == 0

    def test_fail(self, tmp_path):
        script = _make_exit_script(tmp_path, "fail.py", "import sys; sys.exit(1)")
        result = run_one_test(
            {"name": "fail", "command": [str(script)], "labels": []},
            tmp_path,
        )
        assert result.status == FAIL
        assert result.returncode == 1

    def test_should_fail_inverts_result(self, tmp_path):
        script = _make_exit_script(tmp_path, "fail.py", "import sys; sys.exit(1)")
        result = run_one_test(
            {
                "name": "xfail",
                "command": [str(script)],
                "should_fail": True,
                "labels": [],
            },
            tmp_path,
        )
        assert result.status == PASS

    def test_timeout(self, tmp_path):
        script = _make_exit_script(tmp_path, "slow.py", "import time; time.sleep(5)")
        result = run_one_test(
            {
                "name": "slow",
                "command": [str(script)],
                "timeout": 0.3,
                "labels": [],
            },
            tmp_path,
        )
        assert result.status == TIMEOUT

    def test_program_not_found(self, tmp_path):
        result = run_one_test(
            {"name": "missing", "command": ["does_not_exist_xyz"], "labels": []},
            tmp_path,
        )
        assert result.status == ERROR

    def test_disabled_is_skipped(self, tmp_path):
        result = run_one_test(
            {
                "name": "off",
                "command": ["never_runs"],
                "disabled": True,
                "labels": [],
            },
            tmp_path,
        )
        assert result.status == SKIP

    def test_env_vars_passed_to_subprocess(self, tmp_path):
        # The script returns 0 if MY_VAR=hello, else 1.
        script = _make_exit_script(
            tmp_path,
            "envcheck.py",
            "import os, sys\nsys.exit(0 if os.environ.get('MY_VAR') == 'hello' else 1)",
        )
        result = run_one_test(
            {
                "name": "envcheck",
                "command": [str(script)],
                "env": {"MY_VAR": "hello"},
                "labels": [],
            },
            tmp_path,
        )
        assert result.status == PASS


# ----- JUnit XML -----------------------------------------------------------


class TestJUnitOutput:
    def test_writes_well_formed_xml(self, tmp_path):
        results = [
            TestResult(name="ok", status=PASS, duration=0.01),
            TestResult(
                name="bad",
                status=FAIL,
                duration=0.02,
                stderr="boom",
                message="non-zero",
            ),
            TestResult(name="off", status=SKIP, duration=0.0, message="disabled"),
        ]
        out = tmp_path / "junit.xml"
        write_junit(out, "demo", results)

        root = ET.fromstring(out.read_text())
        assert root.tag == "testsuites"
        suite = root.find("testsuite")
        assert suite is not None
        assert suite.attrib["tests"] == "3"
        assert suite.attrib["failures"] == "1"
        cases = suite.findall("testcase")
        assert [c.attrib["name"] for c in cases] == ["ok", "bad", "off"]
        assert cases[1].find("failure") is not None
        assert cases[2].find("skipped") is not None


# ----- main() CLI integration ----------------------------------------------


class TestCLIMain:
    def test_all_pass_returns_zero(self, tmp_path, capsys):
        ok = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        _make_manifest(
            tmp_path,
            [
                {"name": "a", "command": [str(ok)], "labels": []},
                {"name": "b", "command": [str(ok)], "labels": []},
            ],
        )
        rc = main(["--manifest", str(tmp_path / "tests.json"), "-j", "1", "--no-color"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "all tests passed" in captured.out

    def test_any_fail_returns_one(self, tmp_path, capsys):
        bad = _make_exit_script(tmp_path, "bad.py", "import sys; sys.exit(1)")
        _make_manifest(
            tmp_path,
            [
                {"name": "broken", "command": [str(bad)], "labels": []},
            ],
        )
        rc = main(["--manifest", str(tmp_path / "tests.json"), "-j", "1", "--no-color"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAILED" in captured.out

    def test_list_mode_does_not_execute(self, tmp_path, capsys):
        # Pointing at a nonexistent binary would fail if it actually ran.
        _make_manifest(
            tmp_path,
            [
                {"name": "alpha", "command": ["nonexistent"], "labels": ["unit"]},
            ],
        )
        rc = main(["--manifest", str(tmp_path / "tests.json"), "--list", "--no-color"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "alpha" in captured.out

    def test_label_filter(self, tmp_path, capsys):
        ok = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        _make_manifest(
            tmp_path,
            [
                {"name": "fast.a", "command": [str(ok)], "labels": ["fast"]},
                {"name": "slow.b", "command": [str(ok)], "labels": ["slow"]},
            ],
        )
        rc = main(
            [
                "--manifest",
                str(tmp_path / "tests.json"),
                "-L",
                "fast",
                "--no-color",
                "-j",
                "1",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "fast.a" in out
        assert "slow.b" not in out

    def test_junit_output_is_written(self, tmp_path, capsys):
        ok = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        _make_manifest(
            tmp_path,
            [{"name": "alpha", "command": [str(ok)], "labels": []}],
        )
        junit = tmp_path / "junit.xml"
        rc = main(
            [
                "--manifest",
                str(tmp_path / "tests.json"),
                "--junit",
                str(junit),
                "--no-color",
                "-j",
                "1",
            ]
        )
        capsys.readouterr()
        assert rc == 0
        assert junit.is_file()
        root = ET.fromstring(junit.read_text())
        assert root.tag == "testsuites"

    def test_missing_manifest_returns_two(self, tmp_path, capsys):
        # Don't put a manifest anywhere on the search path.
        # Use a non-existent path so the runner can't find one.
        rc = main(
            [
                "--manifest",
                str(tmp_path / "does_not_exist.json"),
                "--no-color",
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "no tests.json" in err or "failed to read" in err


# ----- Dependency graph and scheduling -------------------------------------


class TestValidateDeps:
    def test_unknown_dep_raises(self):
        tests = [
            {"name": "a", "command": [], "depends_on": ["missing"]},
        ]
        with pytest.raises(ValueError, match="not a defined test"):
            _validate_deps(tests)

    def test_cycle_detection(self):
        tests = [
            {"name": "a", "command": [], "depends_on": ["b"]},
            {"name": "b", "command": [], "depends_on": ["a"]},
        ]
        with pytest.raises(ValueError, match="Cycle in depends_on"):
            _validate_deps(tests)

    def test_self_loop_detected(self):
        tests = [{"name": "a", "command": [], "depends_on": ["a"]}]
        with pytest.raises(ValueError, match="Cycle"):
            _validate_deps(tests)

    def test_acyclic_is_fine(self):
        tests = [
            {"name": "a", "command": []},
            {"name": "b", "command": [], "depends_on": ["a"]},
            {"name": "c", "command": [], "depends_on": ["a", "b"]},
        ]
        _validate_deps(tests)  # should not raise


class TestExpandFilterWithDeps:
    def test_dep_is_auto_included(self):
        all_tests = [
            {"name": "setup", "labels": ["fixture"]},
            {"name": "api.a", "labels": ["api"], "depends_on": ["setup"]},
            {"name": "other", "labels": ["other"]},
        ]
        # Filter to api.a only — setup should be folded back in.
        filtered = [all_tests[1]]
        result = expand_filter_with_deps(filtered, all_tests)
        names = [t["name"] for t in result]
        assert "setup" in names
        assert "api.a" in names
        assert "other" not in names
        # Manifest order is preserved
        assert names.index("setup") < names.index("api.a")

    def test_transitive_deps_included(self):
        all_tests = [
            {"name": "a"},
            {"name": "b", "depends_on": ["a"]},
            {"name": "c", "depends_on": ["b"]},
        ]
        filtered = [all_tests[2]]
        result = expand_filter_with_deps(filtered, all_tests)
        assert {t["name"] for t in result} == {"a", "b", "c"}


class TestRunAllWithDeps:
    def test_dep_failure_skips_dependent(self, tmp_path):
        good = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        bad = _make_exit_script(tmp_path, "bad.py", "import sys; sys.exit(1)")
        tests = [
            {"name": "root", "command": [str(bad)], "labels": []},
            {
                "name": "child",
                "command": [str(good)],
                "depends_on": ["root"],
                "labels": [],
            },
        ]
        results = run_all(
            tests,
            tmp_path,
            jobs=2,
            stop_on_fail=False,
            on_start=lambda _t: None,
            on_finish=lambda _t, _r: None,
        )
        by_name = {r.name: r for r in results}
        assert by_name["root"].status == FAIL
        assert by_name["child"].status == SKIP
        assert "dep failed" in by_name["child"].message

    def test_dep_pass_allows_dependent(self, tmp_path):
        ok = _make_exit_script(tmp_path, "ok.py", "import sys; sys.exit(0)")
        tests = [
            {"name": "root", "command": [str(ok)], "labels": []},
            {
                "name": "child",
                "command": [str(ok)],
                "depends_on": ["root"],
                "labels": [],
            },
        ]
        results = run_all(
            tests,
            tmp_path,
            jobs=2,
            stop_on_fail=False,
            on_start=lambda _t: None,
            on_finish=lambda _t, _r: None,
        )
        assert all(r.status == PASS for r in results)


# ----- Test-case discovery --------------------------------------------------


def _make_lister(tmp_path, name, list_output):
    """Build a tiny Python script that prints `list_output` for any args."""
    body = f"import sys\nsys.stdout.write({list_output!r})\nsys.exit(0)\n"
    return _make_exit_script(tmp_path, name, body)


class TestDiscoverParsers:
    def test_gtest_parser(self, tmp_path):
        lister = _make_lister(
            tmp_path,
            "fake_gtest.py",
            "MathSuite.\n  Adds\n  Subs  # GetParam() = 0\nStringSuite.\n  Concat\n",
        )
        cases = _discover_gtest(str(lister), tmp_path, dict(os.environ))
        assert cases == [
            ("MathSuite.Adds", ["--gtest_filter=MathSuite.Adds"]),
            ("MathSuite.Subs", ["--gtest_filter=MathSuite.Subs"]),
            ("StringSuite.Concat", ["--gtest_filter=StringSuite.Concat"]),
        ]

    def test_doctest_parser(self, tmp_path):
        lister = _make_lister(
            tmp_path,
            "fake_doctest.py",
            "[doctest] listing all test case names\n"
            "===\n"
            "first case\n"
            "second case\n"
            "===\n"
            "[doctest] unskipped: 2\n",
        )
        cases = _discover_doctest(str(lister), tmp_path, dict(os.environ))
        names = [c[0] for c in cases]
        assert names == ["first case", "second case"]
        assert cases[0][1] == ["--test-case=first case"]

    def test_catch2_parser_skips_hidden(self, tmp_path):
        lister = _make_lister(
            tmp_path,
            "fake_catch.py",
            "first\nsecond\n~hidden\n",
        )
        cases = _discover_catch2(str(lister), tmp_path, dict(os.environ))
        assert [c[0] for c in cases] == ["first", "second"]
        # Catch2 uses positional test-name args
        assert cases[0][1] == ["first"]


class TestExpandDiscovered:
    def test_expansion_replaces_parent(self, tmp_path):
        lister = _make_lister(
            tmp_path,
            "fake.py",
            "Suite.\n  A\n  B\n",
        )
        tests = [
            {
                "name": "parent",
                "command": [str(lister)],
                "discover": "gtest",
                "labels": ["unit"],
                "depends_on": [],
            }
        ]
        expanded, parent_map = expand_discovered_tests(tests, tmp_path)
        names = [t["name"] for t in expanded]
        assert names == ["parent.Suite.A", "parent.Suite.B"]
        assert parent_map == {
            "parent": ["parent.Suite.A", "parent.Suite.B"],
        }
        # Children inherit labels and pick up the per-case filter.
        assert expanded[0]["labels"] == ["unit"]
        assert "--gtest_filter=Suite.A" in expanded[0]["command"]

    def test_depends_on_parent_rewritten_to_children(self, tmp_path):
        lister = _make_lister(tmp_path, "f.py", "S.\n  A\n  B\n")
        tests = [
            {
                "name": "parent",
                "command": [str(lister)],
                "discover": "gtest",
                "labels": [],
            },
            {
                "name": "after",
                "command": [str(lister)],
                "depends_on": ["parent"],
                "labels": [],
            },
        ]
        expanded, _ = expand_discovered_tests(tests, tmp_path)
        after = next(t for t in expanded if t["name"] == "after")
        # The original "parent" name doesn't exist post-expansion;
        # it should have been rewritten to the children.
        assert set(after["depends_on"]) == {"parent.S.A", "parent.S.B"}

    def test_discovery_failure_falls_back(self, tmp_path):
        # Point at a binary that doesn't exist.
        tests = [
            {
                "name": "missing",
                "command": ["does_not_exist_xyz_no_really"],
                "discover": "gtest",
                "labels": [],
            }
        ]
        expanded, parent_map = expand_discovered_tests(tests, tmp_path)
        # Falls back to the original entry as a single test.
        assert len(expanded) == 1
        assert expanded[0]["name"] == "missing"
        assert parent_map == {}


# Skip the executable-script-based tests on Windows: the
# shebang-execution approach doesn't apply. The CI matrix runs the same
# tests under WSL/cygwin paths separately if needed.
if os.name == "nt":
    pytest.skip(
        "test_runner subprocess tests use POSIX exec semantics",
        allow_module_level=True,
    )
