# SPDX-License-Identifier: MIT
"""Build script demonstrating the Test() builder.

This example shows:
- Declaring tests with ``project.Test(...)``
- A test running a script, with a built program and a data file as arguments
- Filtering tests by ``labels``
- Marking a test ``should_fail=True`` for expected-failure assertions
- Marking a test ``disabled=True`` so the runner records it as skipped
- Letting a test skip itself at run time with ``skip_returncode``
- Chaining tests with ``depends_on`` so fixtures gate dependents
- Adjusting test properties after the fact with ``set_test_property``

Run::

    pcons                 # builds the test program
    ninja test            # runs all tests (fails if any test fails)
    pcons test            # runs the tests directly (same effect)
    pcons test -L unit    # only run "unit"-labeled tests
    pcons test --list     # show what would run, without running it
"""

import os
import sys
from pathlib import Path

from pcons import Project, set_test_property

project = Project("unit_tests", build_dir=os.environ.get("PCONS_BUILD_DIR", "build"))
env = project.Environment(toolchain="c")

test_prog = project.Program(
    "test_math",
    env,
    sources=["src/math.c", "src/test_math.c"],
)

# Common case: pass an exit-code test against a built program.
unit_add = project.Test("math.add", test_prog, args=["add"], labels=["unit", "fast"])
unit_mul = project.Test("math.mul", test_prog, args=["mul"], labels=["unit", "fast"])

# A test can run a script that drives a built program. In `args`, a
# Target is the file it builds and a Path is a file in the source tree,
# each written as the test sees it from where it runs; the test depends on
# the Target, so `test-build` builds it.
project.Test(
    "math.cases",
    sys.executable,
    args=[Path("check_cases.py"), test_prog, Path("cases.txt")],
    labels=["unit"],
)

# `should_fail` inverts the pass/fail interpretation. Use it for XFAIL
# tests (e.g., to verify your build correctly rejects bad input).
project.Test(
    "math.expected_failure",
    test_prog,
    args=["fail"],
    labels=["xfail"],
    should_fail=True,
)

# `disabled=True` records the test but tells the runner to skip it.
# Often used to keep slow/flaky tests visible without running them
# by default.
project.Test(
    "math.slow_placeholder",
    test_prog,
    args=["add"],
    labels=["slow"],
    disabled=True,
)

# `skip_returncode` lets a test decide at run time that it can't run
# (a missing tool, say): exiting with that code reports it as skipped,
# not failed.
project.Test(
    "math.needs_tool",
    test_prog,
    args=["skip"],
    labels=["unit"],
    skip_returncode=77,
)

# `depends_on` chains tests. `math.regression` only runs if both
# math.add and math.mul pass first — useful for fixture-style setups
# where downstream tests need the basics working.
project.Test(
    "math.regression",
    test_prog,
    args=["add"],
    depends_on=["math.add", "math.mul"],
    labels=["unit"],
)

# `set_test_property()` lets you adjust a test after it's been created
# — handy in loops or when the right value depends on later context.
for t in (unit_add, unit_mul):
    set_test_property(t, "timeout", 10.0)
