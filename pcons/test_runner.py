# SPDX-License-Identifier: MIT
"""Test runner for ``pcons test`` and ``ninja test``.

Reads the JSON manifest written by the build (``<build_dir>/tests.json``)
and executes each test as a subprocess. Pure stdlib — no test framework
dependency.

The runner is invoked in two ways:

1. Directly by the user: ``pcons test`` (from anywhere — searches up for
   the manifest) or ``cd build && pcons test``.
2. From Ninja: ``ninja test`` runs a rule that invokes
   ``python -m pcons.test_runner --manifest=tests.json``.

Both paths share the same implementation: :func:`main`.

Output protocol is exit-code only in v1. Future versions may parse TAP
or gtest XML on a per-test basis (``protocol`` field on the spec) but
the runner contract — exit 0 means pass — is forward-compatible.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("pcons.test")


# Result kinds. Strings on purpose — they end up verbatim in JUnit output.
PASS = "pass"
FAIL = "fail"
TIMEOUT = "timeout"
SKIP = "skip"
ERROR = "error"


@dataclass
class TestResult:
    """The outcome of running one test."""

    # Stop pytest from trying to collect this dataclass as a test class
    # (its name starts with "Test" so the auto-discovery picks it up).
    __test__ = False

    name: str
    status: str
    duration: float = 0.0
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)


# ----- ANSI color helpers ---------------------------------------------------

_COLORS = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _color(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_COLORS[color]}{text}{_COLORS['reset']}"


# ----- Manifest discovery ---------------------------------------------------


def find_manifest(start: Path) -> Path | None:
    """Walk upward from *start* looking for a tests.json.

    Checks ``start/tests.json``, then ``start/build/tests.json``, then
    repeats one directory up, and so on until the filesystem root. This
    is robust to the user running ``pcons test`` from their project root
    or from inside the build directory.
    """
    current = start.resolve()
    while True:
        for candidate in (current / "tests.json", current / "build" / "tests.json"):
            if candidate.is_file():
                return candidate
        if current.parent == current:
            return None
        current = current.parent


def load_manifest(path: Path) -> tuple[dict, list[dict]]:
    """Parse a manifest file. Returns (meta, tests).

    ``meta`` is the manifest header (version, project, build_dir);
    ``tests`` is the raw list of test dicts (not TestSpec — the runner
    only needs the JSON fields).
    """
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: manifest root must be a JSON object")
    tests = data.get("tests", [])
    if not isinstance(tests, list):
        raise ValueError(f"{path}: 'tests' must be a list")
    return data, tests


# ----- Filtering ------------------------------------------------------------


def filter_tests(
    tests: list[dict],
    *,
    include_labels: list[str],
    exclude_labels: list[str],
    include_regex: str | None,
    exclude_regex: str | None,
) -> list[dict]:
    """Apply CLI filters in CTest-style precedence.

    A test must (a) match at least one include filter (or have none),
    and (b) match no exclude filter, for both labels and name regex.
    Label matching is by substring against any of the test's labels;
    name matching is via :func:`re.search` against the test name.
    """
    include_re = re.compile(include_regex) if include_regex else None
    exclude_re = re.compile(exclude_regex) if exclude_regex else None

    def keep(t: dict) -> bool:
        name = t.get("name", "")
        labels = t.get("labels", []) or []
        if include_labels:
            if not any(any(inc in lbl for lbl in labels) for inc in include_labels):
                return False
        if exclude_labels:
            if any(any(exc in lbl for lbl in labels) for exc in exclude_labels):
                return False
        if include_re and not include_re.search(name):
            return False
        if exclude_re and exclude_re.search(name):
            return False
        return True

    return [t for t in tests if keep(t)]


# ----- Test-case discovery (gtest / doctest / catch2) ----------------------
#
# When a test entry has `discover` set, the runner invokes the binary's
# "list test cases" flag at run time, parses the output, and expands the
# entry into one test per discovered case. This avoids requiring the
# build description to know the case names upfront (which CMake addresses
# at build time via gtest_discover_tests / doctest_discover_tests).


def _resolve_program_for_discovery(binary: str, build_dir: Path) -> str:
    """Anchor a build-dir-relative program path the same way run_one_test does."""
    p = Path(binary)
    if p.is_absolute():
        return binary
    return str((build_dir / binary).resolve())


def _discover_gtest(
    binary: str, cwd: Path, env: dict[str, str]
) -> list[tuple[str, list[str]]]:
    """List cases in a googletest binary.

    Returns ``[(case_name, filter_args), ...]`` where ``filter_args`` is
    the argument list that runs that single case.

    Output looks like::

        Suite1.
          Test1
          Test2  # GetParam() = 0
        Suite2.
          Test3
    """
    result = subprocess.run(
        [binary, "--gtest_list_tests"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"--gtest_list_tests failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    cases: list[tuple[str, list[str]]] = []
    current_suite: str | None = None
    for raw in result.stdout.splitlines():
        if not raw.strip():
            continue
        # Suites are flush-left and end with a "."
        if not raw.startswith(" ") and raw.rstrip().endswith("."):
            current_suite = raw.rstrip().rstrip(".")
            continue
        if current_suite is None:
            continue
        # Test names are indented; strip "# arg = X" trailers on
        # parameterized tests so we get the bare TEST_P case name.
        name = raw.strip().split("#", 1)[0].strip()
        if not name:
            continue
        full = f"{current_suite}.{name}"
        cases.append((full, [f"--gtest_filter={full}"]))
    return cases


def _discover_doctest(
    binary: str, cwd: Path, env: dict[str, str]
) -> list[tuple[str, list[str]]]:
    """List cases in a doctest binary via ``--list-test-cases``.

    Output is bracketed by ``[doctest]`` banner lines and a row of ``=``;
    everything between is a case name (one per line).
    """
    result = subprocess.run(
        [binary, "--list-test-cases", "--no-version"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"--list-test-cases failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    cases: list[tuple[str, list[str]]] = []
    for raw in result.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("[doctest]"):
            continue
        if line.startswith("="):
            continue
        cases.append((line, [f"--test-case={line}"]))
    return cases


def _discover_catch2(
    binary: str, cwd: Path, env: dict[str, str]
) -> list[tuple[str, list[str]]]:
    """List cases in a Catch2 binary via ``--list-test-names-only``.

    Names prefixed with ``~`` are hidden tests (Catch2 convention) and
    are skipped.
    """
    result = subprocess.run(
        [binary, "--list-test-names-only"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"--list-test-names-only failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    cases: list[tuple[str, list[str]]] = []
    for raw in result.stdout.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("~"):
            continue
        # Catch2 takes a single positional test-name argument.
        cases.append((line, [line]))
    return cases


_DISCOVERERS: dict[
    str, Callable[[str, Path, dict[str, str]], list[tuple[str, list[str]]]]
] = {
    "gtest": _discover_gtest,
    "doctest": _discover_doctest,
    "catch2": _discover_catch2,
}


def expand_discovered_tests(
    tests: list[dict], build_dir: Path
) -> tuple[list[dict], dict[str, list[str]]]:
    """Replace each test with ``discover`` set by one entry per case.

    Returns the expanded list plus a ``parent_name -> [child_names]``
    map. The map lets callers fix up ``depends_on`` entries that named
    the original parent: each parent reference is replaced by all of
    its children, so dependent tests still wait for the right things.

    If discovery fails for a test (binary not found, framework not
    cooperative), the original entry is preserved and a warning printed;
    the test will then run as a single pass/fail.
    """
    expanded: list[dict] = []
    parent_to_children: dict[str, list[str]] = {}

    for test in tests:
        protocol = test.get("discover")
        if not protocol:
            expanded.append(test)
            continue
        if protocol not in _DISCOVERERS:
            sys.stderr.write(
                f"warning: test {test['name']!r}: "
                f"unknown discover protocol {protocol!r}; running as single test.\n"
            )
            expanded.append(test)
            continue

        binary = _resolve_program_for_discovery(test["command"][0], build_dir)
        env_overrides = test.get("env", {}) or {}
        proc_env = os.environ.copy()
        proc_env.update({k: str(v) for k, v in env_overrides.items()})
        cwd = Path(test.get("cwd") or build_dir)

        try:
            cases = _DISCOVERERS[protocol](binary, cwd, proc_env)
        except (RuntimeError, FileNotFoundError, OSError) as e:
            sys.stderr.write(
                f"warning: discovery failed for {test['name']!r}: {e}\n"
                "  → falling back to running the binary as a single test.\n"
            )
            expanded.append(test)
            continue

        if not cases:
            sys.stderr.write(
                f"warning: no cases discovered for {test['name']!r}; skipping.\n"
            )
            continue

        base_name = test["name"]
        base_args = test["command"][1:]
        child_names: list[str] = []
        for case_name, case_filter in cases:
            child = dict(test)
            child["name"] = f"{base_name}.{case_name}"
            child["command"] = [test["command"][0], *base_args, *case_filter]
            child["discover"] = None
            expanded.append(child)
            child_names.append(child["name"])
        parent_to_children[base_name] = child_names

    if parent_to_children:
        # Rewrite depends_on so references to the (now-vanished) parent
        # expand to every child. This preserves fixture semantics.
        for test in expanded:
            deps = test.get("depends_on") or ()
            if not deps:
                continue
            new_deps: list[str] = []
            for d in deps:
                if d in parent_to_children:
                    new_deps.extend(parent_to_children[d])
                else:
                    new_deps.append(d)
            test["depends_on"] = new_deps

    return expanded, parent_to_children


# ----- Single-test execution ------------------------------------------------


def _resolve_cwd(test: dict, build_dir: Path) -> Path:
    """The directory the test process runs in.

    The manifest's ``cwd`` is preferred when set; otherwise the build
    directory (which is also where manifest-relative program paths are
    rooted) is used.
    """
    cwd = test.get("cwd")
    if cwd:
        return Path(cwd)
    return build_dir


def run_one_test(test: dict, build_dir: Path) -> TestResult:
    """Execute one test and return its TestResult.

    The command's first element is the program; if it's a
    build-dir-relative path we prefix ``./`` so POSIX shells (or rather,
    POSIX path resolution rules) actually find it. Absolute paths and
    paths with a directory prefix are left alone.
    """
    name = test["name"]
    labels = tuple(test.get("labels", []) or [])

    if test.get("disabled"):
        return TestResult(name=name, status=SKIP, labels=labels, message="disabled")

    command = list(test["command"])
    if command:
        prog = command[0]
        prog_path = Path(prog)
        if not prog_path.is_absolute() and "/" not in prog and "\\" not in prog:
            # Bare name like "math_test" — resolve against build_dir.
            command[0] = str((build_dir / prog).resolve())
        elif not prog_path.is_absolute():
            # Relative with a directory, e.g. "obj.foo/runner" — same idea.
            command[0] = str((build_dir / prog).resolve())

    env_overrides = test.get("env", {}) or {}
    proc_env = os.environ.copy()
    proc_env.update({k: str(v) for k, v in env_overrides.items()})

    timeout = test.get("timeout")
    should_fail = bool(test.get("should_fail"))
    cwd = _resolve_cwd(test, build_dir)

    start = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=proc_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        duration = time.monotonic() - start
        return TestResult(
            name=name,
            status=TIMEOUT,
            duration=duration,
            stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
            stderr=e.stderr or "" if isinstance(e.stderr, str) else "",
            message=f"Killed after {timeout}s",
            labels=labels,
        )
    except FileNotFoundError as e:
        duration = time.monotonic() - start
        return TestResult(
            name=name,
            status=ERROR,
            duration=duration,
            message=f"Program not found: {e.filename}",
            labels=labels,
        )
    except OSError as e:
        duration = time.monotonic() - start
        return TestResult(
            name=name,
            status=ERROR,
            duration=duration,
            message=f"Failed to launch: {e}",
            labels=labels,
        )

    duration = time.monotonic() - start
    passed = (result.returncode == 0) != should_fail
    return TestResult(
        name=name,
        status=PASS if passed else FAIL,
        duration=duration,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        labels=labels,
    )


# ----- Parallel orchestration ----------------------------------------------


def _is_failed(result: TestResult) -> bool:
    """A test result counts as failing for the purposes of blocking dependents."""
    return result.status in (FAIL, TIMEOUT, ERROR)


def _validate_deps(tests: list[dict]) -> None:
    """Sanity-check the depends_on graph: no unknown deps, no cycles.

    Raises ValueError on any problem; the runner converts that to a
    user-visible error message.
    """
    known = {t["name"] for t in tests}
    deps_of = {t["name"]: list(t.get("depends_on") or ()) for t in tests}

    for name, deps in deps_of.items():
        for d in deps:
            if d not in known:
                raise ValueError(
                    f"Test {name!r} depends_on {d!r} which is not a defined test."
                )

    # DFS cycle detection.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(deps_of, WHITE)

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        for d in deps_of[node]:
            if color[d] == GRAY:
                cycle = " -> ".join([*stack[stack.index(d) :], d])
                raise ValueError(f"Cycle in depends_on graph: {cycle}")
            if color[d] == WHITE:
                visit(d, [*stack, d])
        color[node] = BLACK

    for n in deps_of:
        if color[n] == WHITE:
            visit(n, [n])


def expand_filter_with_deps(filtered: list[dict], all_tests: list[dict]) -> list[dict]:
    """Auto-include tests pulled in transitively by ``depends_on``.

    When the user filters with ``-L`` or ``-R``, deps of selected tests
    are folded back in. Without this, ``pcons test -L api`` would skip
    the ``setup_server`` fixture and every selected test would fail.

    Manifest order is preserved in the result.
    """
    by_name = {t["name"]: t for t in all_tests}
    visible = {t["name"] for t in filtered}
    frontier = set(visible)
    while frontier:
        nxt: set[str] = set()
        for name in frontier:
            test = by_name.get(name)
            if test is None:
                continue
            for d in test.get("depends_on") or ():
                if d not in visible and d in by_name:
                    visible.add(d)
                    nxt.add(d)
        frontier = nxt
    return [t for t in all_tests if t["name"] in visible]


def run_all(
    tests: list[dict],
    build_dir: Path,
    *,
    jobs: int,
    stop_on_fail: bool,
    on_start: Callable[[dict], None],
    on_finish: Callable[[dict, TestResult], None],
) -> list[TestResult]:
    """Run every test, honoring depends_on and serial / parallel constraints.

    Scheduler:
      - A test becomes *ready* when every entry in its ``depends_on`` has
        a final result.
      - A ready test whose dependency failed is reported as ``SKIP`` with
        ``message="dep failed: ..."``; the runner does not invoke it.
      - A ready test marked ``serial`` runs alone (no other tests in
        flight). Non-serial tests share a pool of ``jobs`` workers.
      - With ``stop_on_fail``, after the first failure no new tests are
        launched and remaining ones are emitted as ``SKIP``.

    ``jobs <= 0`` follows ninja's ``-j0`` convention of "unlimited": every
    ready test may launch at once (capped, in practice, at one worker per
    test — there's no point in a bigger pool than that).

    The returned list preserves manifest order so report output is stable
    regardless of how tests interleave at runtime.
    """
    by_name = {t["name"]: t for t in tests}
    deps_of = {t["name"]: list(t.get("depends_on") or ()) for t in tests}
    _validate_deps(tests)

    # Normalize once, and use this value for both the pool size and the
    # launch gate below — using the raw (possibly non-positive) `jobs`
    # for the gate while sizing the pool off `max(1, jobs)` meant `-j0`
    # (or a negative value) made `len(running) >= jobs` always true, so
    # no non-serial test was ever submitted.
    effective_jobs = jobs if jobs > 0 else max(len(tests), 1)

    results: dict[str, TestResult] = {}
    pending: set[str] = set(by_name)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=effective_jobs)
    running: dict[concurrent.futures.Future, str] = {}
    serial_in_flight = False
    stopped = False

    def schedule() -> None:
        """Launch as many ready tests as the constraints allow."""
        nonlocal serial_in_flight, stopped
        if stop_on_fail and any(_is_failed(r) for r in results.values()):
            stopped = True

        if stopped:
            for name in sorted(pending):
                test = by_name[name]
                pending.discard(name)
                r = TestResult(
                    name=name,
                    status=SKIP,
                    message="stopped on prior failure",
                    labels=tuple(test.get("labels", []) or []),
                )
                results[name] = r
                on_finish(test, r)
            return

        # Iterate over a stable snapshot — pending mutates inside the loop.
        for name in [t["name"] for t in tests if t["name"] in pending]:
            test = by_name[name]
            deps = deps_of[name]
            unmet = [d for d in deps if d not in results]
            if unmet:
                continue
            failed = [d for d in deps if _is_failed(results[d])]
            if failed:
                pending.discard(name)
                r = TestResult(
                    name=name,
                    status=SKIP,
                    message=f"dep failed: {', '.join(failed)}",
                    labels=tuple(test.get("labels", []) or []),
                )
                results[name] = r
                on_finish(test, r)
                continue
            # Ready and not skipped. Check capacity.
            if test.get("serial"):
                if running or serial_in_flight:
                    continue
                pending.discard(name)
                serial_in_flight = True
                on_start(test)
                fut = pool.submit(run_one_test, test, build_dir)
                running[fut] = name
                # Don't launch anything else this pass — serial is exclusive.
                return
            if serial_in_flight:
                continue
            if len(running) >= effective_jobs:
                break
            pending.discard(name)
            on_start(test)
            fut = pool.submit(run_one_test, test, build_dir)
            running[fut] = name

    try:
        while pending or running:
            schedule()
            if not running:
                # No tests running and we couldn't schedule any — every
                # remaining test must be blocked by something. The deps
                # validator already ruled out cycles, so this only fires
                # under stop_on_fail bookkeeping; safe to exit.
                break
            done, _ = concurrent.futures.wait(
                running, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for fut in done:
                name = running.pop(fut)
                test = by_name[name]
                result = fut.result()
                if test.get("serial"):
                    serial_in_flight = False
                results[name] = result
                on_finish(test, result)
    finally:
        pool.shutdown(wait=True)

    # Preserve manifest order in the output; any test still missing is a
    # defensive "didn't run" entry rather than a crash.
    ordered: list[TestResult] = []
    for test in tests:
        name = test["name"]
        if name in results:
            ordered.append(results[name])
        else:
            ordered.append(
                TestResult(
                    name=name,
                    status=SKIP,
                    message="not run",
                    labels=tuple(test.get("labels", []) or []),
                )
            )
    return ordered


# ----- Output: human-readable ----------------------------------------------


def _status_label(status: str, color: bool) -> str:
    table = {
        PASS: ("Passed", "green"),
        FAIL: ("Failed", "red"),
        TIMEOUT: ("Timeout", "red"),
        ERROR: ("Error", "red"),
        SKIP: ("Skipped", "yellow"),
    }
    label, col = table.get(status, ("?", "yellow"))
    return _color(f"{label:>8s}", col, color)


def print_summary(
    project_name: str,
    results: list[TestResult],
    color: bool,
    verbose: bool,
) -> None:
    """Print the CTest-style end-of-run summary."""
    total = len(results)
    passed = sum(1 for r in results if r.status == PASS)
    failed = [r for r in results if r.status in (FAIL, TIMEOUT, ERROR)]
    skipped = [r for r in results if r.status == SKIP]

    print()
    pct = (100 * passed // total) if total else 0
    summary = f"{pct}% tests passed, {len(failed)} tests failed out of {total}"
    if skipped:
        summary += f" ({len(skipped)} skipped)"
    print(summary)

    if failed:
        print()
        print(_color("The following tests FAILED:", "red", color))
        for r in failed:
            extra = f" ({r.message})" if r.message else ""
            print(f"  - {r.name} [{r.status}]{extra}")
            if verbose and r.stderr:
                # Indent the stderr block so it visually nests under the test.
                indented = "\n".join("      " + line for line in r.stderr.splitlines())
                print(indented)
    if not failed:
        print(_color(f"Project: {project_name} — all tests passed.", "green", color))


# ----- Output: JUnit XML ----------------------------------------------------

# XML 1.0 forbids most C0 control characters in text content (tab, LF, CR
# are the only ones allowed below 0x20). Captured stdout/stderr from a
# test process can contain anything — e.g. a test that crashes mid binary
# write — so it must be scrubbed before being embedded, or ET.write()
# produces a file that no XML parser will accept.
_XML_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_xml_text(text: str) -> str:
    """Strip control characters that are illegal in XML 1.0 text/attributes."""
    return _XML_ILLEGAL_CHARS_RE.sub("", text)


def write_junit(path: Path, project_name: str, results: list[TestResult]) -> None:
    """Write a single-suite JUnit XML report.

    The schema matches what Jenkins, GitLab CI, and most other CI
    systems consume by default. One ``<testsuite>`` per project,
    ``<testcase>`` per test, with ``<failure>`` / ``<error>`` /
    ``<skipped>`` children where appropriate.
    """
    total = len(results)
    failures = sum(1 for r in results if r.status == FAIL)
    errors = sum(1 for r in results if r.status in (TIMEOUT, ERROR))
    skipped = sum(1 for r in results if r.status == SKIP)
    total_time = sum(r.duration for r in results)

    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        name=project_name,
        tests=str(total),
        failures=str(failures),
        errors=str(errors),
        skipped=str(skipped),
        time=f"{total_time:.3f}",
    )
    for r in results:
        case = ET.SubElement(
            suite,
            "testcase",
            name=r.name,
            classname=project_name,
            time=f"{r.duration:.3f}",
        )
        if r.status == FAIL:
            f = ET.SubElement(
                case, "failure", message=_sanitize_xml_text(r.message or "test failed")
            )
            f.text = _sanitize_xml_text(r.stderr or r.stdout or "")
        elif r.status in (TIMEOUT, ERROR):
            e = ET.SubElement(
                case,
                "error",
                type=r.status,
                message=_sanitize_xml_text(r.message or ""),
            )
            e.text = _sanitize_xml_text(r.stderr or r.stdout or "")
        elif r.status == SKIP:
            ET.SubElement(case, "skipped", message=_sanitize_xml_text(r.message or ""))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ----- CLI -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcons test",
        description="Run tests declared by project.Test() in pcons-build.py.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Path to tests.json (default: searched upward from cwd)",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="Number of parallel tests (default: CPU count)",
    )
    parser.add_argument(
        "-L",
        action="append",
        default=[],
        metavar="LABEL",
        help="Only run tests whose labels contain LABEL (repeatable)",
    )
    parser.add_argument(
        "-LE",
        action="append",
        default=[],
        metavar="LABEL",
        help="Skip tests whose labels contain LABEL (repeatable)",
    )
    parser.add_argument(
        "-R",
        metavar="REGEX",
        help="Only run tests whose name matches REGEX",
    )
    parser.add_argument(
        "-E",
        metavar="REGEX",
        help="Skip tests whose name matches REGEX",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List tests that would run, without running them",
    )
    parser.add_argument(
        "-V",
        "--verbose",
        action="store_true",
        help="Show stdout/stderr for failed tests",
    )
    parser.add_argument(
        "--junit",
        type=Path,
        metavar="FILE",
        help="Write JUnit XML report to FILE",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color in output",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop launching new tests after the first failure",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``pcons test`` and ``python -m pcons.test_runner``."""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Locate manifest
    manifest_path: Path | None = args.manifest
    if manifest_path is None:
        manifest_path = find_manifest(Path.cwd())
    if manifest_path is None or not manifest_path.is_file():
        sys.stderr.write(
            "error: no tests.json found. Run 'pcons generate' first, "
            "or pass --manifest=PATH.\n"
        )
        return 2

    try:
        meta, tests = load_manifest(manifest_path)
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"error: failed to read {manifest_path}: {e}\n")
        return 2

    color = (not args.no_color) and sys.stdout.isatty()
    project_name = meta.get("project", "(unknown)")
    # The manifest is always written into the build directory, so its
    # parent is the canonical anchor for resolving relative program paths.
    # The "build_dir" entry in the manifest is informational only.
    build_dir = manifest_path.parent.resolve()

    # Labels survive discovery expansion unchanged (each discovered case
    # inherits its parent's labels), so -L/-LE can be applied up front,
    # against the pre-discovery entries. This lets a whole discover-enabled
    # binary be skipped by label without ever invoking its "list test
    # cases" flag. Name regexes (-R/-E) can't be applied yet: a discover
    # entry's own name isn't one of the case names it will expand into, so
    # those filters are re-applied below, once expansion has run.
    label_filtered = filter_tests(
        tests,
        include_labels=args.L,
        exclude_labels=args.LE,
        include_regex=None,
        exclude_regex=None,
    )
    label_filtered = expand_filter_with_deps(label_filtered, tests)

    if args.list:
        # --list only needs names, so there's no reason to actually run
        # discovery binaries just to enumerate their cases; list the
        # manifest-level entries instead (annotating discover entries,
        # since their real case names are only known once run).
        listed = filter_tests(
            label_filtered,
            include_labels=[],
            exclude_labels=[],
            include_regex=args.R,
            exclude_regex=args.E,
        )
        print(f"Test project: {project_name} ({len(listed)} tests)")
        for t in listed:
            labels = ",".join(t.get("labels", []) or [])
            label_str = f" [{labels}]" if labels else ""
            discover_str = f" (discover: {t['discover']})" if t.get("discover") else ""
            print(f"  {t['name']}{label_str}{discover_str}")
        return 0

    # Discover test cases inside binaries that asked for it. This rewrites
    # the test list in place: a "discover" entry is replaced by N entries,
    # one per case found by running the binary's listing flag. Only the
    # label-filtered survivors reach this point, so an excluded binary's
    # cases are never enumerated.
    tests, _expansion_map = expand_discovered_tests(label_filtered, build_dir)

    filtered = filter_tests(
        tests,
        include_labels=args.L,
        exclude_labels=args.LE,
        include_regex=args.R,
        exclude_regex=args.E,
    )
    # Auto-include any deps that the filter would otherwise drop, so that
    # `pcons test -L api` still pulls in a `setup_server` fixture.
    filtered = expand_filter_with_deps(filtered, tests)

    if not filtered:
        print(f"Test project: {project_name}: no tests matched.")
        return 0

    print(f"Test project: {project_name} ({len(filtered)} tests)")

    counter = {"started": 0, "finished": 0}
    total = len(filtered)
    pad = len(str(total))

    def on_start(test: dict) -> None:
        counter["started"] += 1
        print(
            _color(
                f"      Start {counter['started']:>{pad}}: {test['name']}",
                "dim",
                color,
            )
        )

    def on_finish(test: dict, result: TestResult) -> None:
        counter["finished"] += 1
        idx = counter["finished"]
        # Right-pad name to a sensible width for visual alignment.
        name_field = f"{test['name']:<40s}"
        status = _status_label(result.status, color)
        dur = f"{result.duration:6.2f}s"
        print(f" {idx:>{pad}}/{total} Test #{idx:>{pad}}: {name_field} {status}  {dur}")

    try:
        results = run_all(
            filtered,
            build_dir,
            jobs=args.jobs,
            stop_on_fail=args.stop_on_fail,
            on_start=on_start,
            on_finish=on_finish,
        )
    except ValueError as e:
        # Dep validation failure (unknown dep, cycle, …). Surface clearly.
        sys.stderr.write(f"error: {e}\n")
        return 2

    print_summary(project_name, results, color, args.verbose)

    if args.junit:
        write_junit(args.junit, project_name, results)
        print(f"Wrote JUnit report: {args.junit}")

    any_failed = any(r.status in (FAIL, TIMEOUT, ERROR) for r in results)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
