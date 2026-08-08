# Testing

Pcons keeps the build-system / test-runner split clean: build scripts
**declare** tests via `project.Test(...)`, the configure step writes a
JSON manifest (`<build_dir>/tests.json`), and a separate runner —
`pcons test` (or `ninja test`) — executes them. This is the same model
CMake uses with CTest, and it keeps pcons itself out of the business
of running things at build time.

## Declaring Tests

```python
test_prog = project.Program("math_test", env, sources=["src/math.c", "src/test_math.c"])

# Most basic: run the program; pass = exit 0.
project.Test("math.add", test_prog, args=["add"], labels=["unit", "fast"])
project.Test("math.mul", test_prog, args=["mul"], labels=["unit", "fast"])

# should_fail=True inverts the exit code (XFAIL-style assertions).
project.Test(
    "math.expected_failure",
    test_prog,
    args=["bad-input"],
    should_fail=True,
    labels=["xfail"],
)

# disabled=True keeps the test in the manifest but always skips it.
project.Test(
    "math.slow", test_prog, args=["heavy"], labels=["slow"], disabled=True, timeout=60
)
```

The full set of fields (all keyword-only):

| Field | Type | Purpose |
|---|---|---|
| `args` | `Sequence[str]` | Arguments passed after the program. |
| `cwd` | `Path \| str \| None` | Working directory; defaults to the build dir. |
| `env` | `dict[str, str] \| None` | Extra environment variables for the test process. |
| `labels` | `Sequence[str]` | Tags for filtering (`unit`, `integration`, `slow`, `fuzz`, ...). |
| `timeout` | `float \| None` | Seconds before the runner kills the test. |
| `should_fail` | `bool` | If True, a non-zero exit code is a pass. |
| `serial` | `bool` | Don't run in parallel with other tests. |
| `disabled` | `bool` | Record the test but always skip. |
| `data` | `Sequence[Path \| str]` | Runtime data files (informational in v1). |
| `depends_on` | `Sequence[str]` | Names of tests that must pass before this one runs. |
| `discover` | `"gtest" \| "doctest" \| "catch2" \| None` | Expand into one entry per test case in the binary. |

`program` can be a `Target` (the typical case — depending on it ensures
`ninja test-build` compiles it first), or a `Path` / `str` to an
existing script or external binary.

## Fixtures and Test Ordering: `depends_on`

`depends_on` lets one test gate another. If a dependency fails (or
times out, errors, or skips because of its own failed dep), all
dependent tests are reported as skipped — no point running them.

```python
# Fixture-style: start a server, run tests against it, stop it.
start = project.Test("server.start", start_script, labels=["fixture"])
project.Test(
    "api.list_users",
    api_test,
    args=["list-users"],
    depends_on=["server.start"],
    labels=["api"],
)
project.Test(
    "api.create_user",
    api_test,
    args=["create-user"],
    depends_on=["server.start"],
    labels=["api"],
)
project.Test(
    "server.stop", stop_script, depends_on=["server.start"], labels=["fixture"]
)
```

When you filter with `-L` / `-R`, deps of selected tests are
auto-included so fixtures keep working:

```bash
pcons test -L api    # still runs server.start (and server.stop)
```

## Test-Case Discovery: `discover`

For binaries built against GoogleTest, doctest, or Catch2, listing
every test case in the Python build script is tedious. Set
`discover=` and the runner queries the binary at run time, expanding
your single `project.Test()` into one entry per test case:

```python
unit_tests = project.Program("unit_tests", env, sources=[...])
project.Test("unit", unit_tests, discover="gtest", labels=["unit"])
```

Run-time output:
```
Test project: myproject (84 tests)
      Start  1: unit.MathSuite.Add
 1/84 Test #1: unit.MathSuite.Add ..................... Passed  0.01s
      Start  2: unit.MathSuite.Subtract
 2/84 Test #2: unit.MathSuite.Subtract ................ Passed  0.01s
 ...
```

Each case becomes a separate test that:

- Inherits the parent's `labels`, `timeout`, `env`, `cwd`, etc.
- Runs the binary with the framework's single-case filter
  (`--gtest_filter=...`, `--test-case=...`, or a positional name).
- Appears in `--list`, JUnit output, and label/regex filters.

Protocols supported:

| `discover=` | Listing flag | Invocation |
|---|---|---|
| `"gtest"` | `--gtest_list_tests` | `<bin> --gtest_filter=Suite.Case` |
| `"doctest"` | `--list-test-cases --no-version` | `<bin> --test-case="<name>"` |
| `"catch2"` | `--list-test-names-only` | `<bin> "<name>"` |

If discovery fails (binary missing, framework not cooperative), the
runner falls back to running the parent as a single test and prints a
warning. References to the discovered parent in another test's
`depends_on` are rewritten to wait on every child.

## Tweaking Tests After Creation: `set_test_property`

Sometimes a property depends on context that isn't known at the
`project.Test()` call site — a slow test that needs a bigger timeout
under a particular toolchain, a label applied to every test in a
generated batch. `set_test_property()` / `set_test_properties()`
update an unresolved test, mirroring CMake's `set_tests_properties()`:

```python
from pcons import set_test_property, set_test_properties

t = project.Test("slow_one", prog)
set_test_property(t, "timeout", 600)

# Bulk: one call, many tests, many properties.
slow_tests = [t1, t2, t3]
set_test_properties(*slow_tests, timeout=600, labels=["slow"])
```

Valid property names are the same kwargs accepted by `project.Test()`:
`args`, `cwd`, `env`, `labels`, `timeout`, `should_fail`, `serial`,
`disabled`, `data`, `depends_on`, `discover`, `program`.

## Running Tests

```bash
ninja test                    # build, then run, all tests
pcons test                    # same effect, runs the test runner directly
pcons test -L unit            # only run "unit"-labeled tests
pcons test -L unit -LE slow   # unit, excluding slow
pcons test -R '^math\.'       # only run tests whose name matches the regex
pcons test --list             # show what would run, without running
pcons test -j 1               # serial mode (default: CPU count)
pcons test --junit out.xml    # emit JUnit XML for CI
pcons test --stop-on-fail     # stop after the first failure
pcons test -V                 # verbose: show stderr from failed tests
```

The runner picks up the manifest from the current build directory (or
walks upward to find one). Exit code is 0 if every selected test
passed, non-zero otherwise — which is why `ninja test` "just works"
for CI failure detection.

## The Test Manifest

`<build_dir>/tests.json` is plain JSON, versioned, and stable:

```json
{
  "version": 1,
  "project": "myproject",
  "build_dir": "build",
  "tests": [
    {
      "name": "math.add",
      "command": ["test_math", "add"],
      "cwd": null,
      "env": {},
      "labels": ["unit", "fast"],
      "timeout": null,
      "should_fail": false,
      "serial": false,
      "disabled": false,
      "data": [],
      "defined_at": "pcons-build.py:34"
    }
  ]
}
```

You can read it, grep it, and feed it to any other runner you like —
nothing in the format depends on the pcons runtime.

## Fuzzing

Pcons doesn't have a dedicated fuzz-test builder — a fuzz target is just
a fuzzer-instrumented `Program` plus one or two `Test`s. The same shape
works for libFuzzer, AFL++, and Honggfuzz; only the build flags and the
campaign invocation change. See `examples/41_fuzzing/` for a complete
libFuzzer harness; the recipes below show the engine-specific bits.

The recommended pattern is two tests per harness:

- a **regression** test that replays a committed seed corpus (fast,
  deterministic, runs on every commit), and
- a **campaign** test that actually fuzzes for a bounded wall-clock time
  (labelled `["fuzz"]` so `pcons test -L fuzz` runs only campaigns and
  `pcons test -LE fuzz` excludes them).

### libFuzzer (clang built-in)

Same `LLVMFuzzerTestOneInput` entrypoint; libFuzzer's `main()` is linked
in by `-fsanitize=fuzzer`.

```python
fuzz_flags = ["-fsanitize=fuzzer,address", "-g", "-O1"]
env.cc.flags.extend(fuzz_flags)
env.link.flags.extend(fuzz_flags)

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test("parser.regression", harness, args=["-runs=0", corpus_dir], timeout=30)
project.Test(
    "parser.campaign",
    harness,
    args=[
        "-create_missing_dirs=1",
        "campaign-corpus",
        corpus_dir,
        "-max_total_time=60",
    ],
    labels=["fuzz"],
    timeout=90,
)
```

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    parse_keyvalue(data, size);
    return 0;
}
```

### AFL++

The same `LLVMFuzzerTestOneInput` entrypoint works in persistent mode.
The compiler driver becomes `afl-clang-fast`; the campaign is run by
`afl-fuzz` rather than the harness binary itself.

```python
# Easiest setup: run pcons with CC=afl-clang-fast (and CXX=afl-clang-fast++).
# Or point a custom toolchain at the AFL++ driver explicitly.

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test(
    "parser.regression",
    "afl-showmap",
    args=[
        "-o",
        "/dev/null",
        "-i",
        corpus_dir,
        "--",
        str(harness.output_nodes[0].path),
        "@@",
    ],
)

project.Test(
    "parser.campaign",
    "afl-fuzz",
    args=[
        "-V",
        "60",
        "-i",
        corpus_dir,
        "-o",
        findings_dir,
        "--",
        str(harness.output_nodes[0].path),
    ],
    labels=["fuzz"],
    timeout=120,
)
```

### Honggfuzz

Same harness; build with `hfuzz-clang` and link against `libhfuzz.a`. The
campaign is run by the `honggfuzz` binary.

```python
# Easiest setup: CC=hfuzz-clang (and CXX=hfuzz-clang++).

harness = project.Program("fuzz_parser", env, sources=["fuzz_parser.c", "parser.c"])

project.Test(
    "parser.campaign",
    "honggfuzz",
    args=[
        "-i",
        corpus_dir,
        "-o",
        findings_dir,
        "--run_time",
        "60",
        "--",
        str(harness.output_nodes[0].path),
    ],
    labels=["fuzz"],
    timeout=120,
)
```

### Conventions

- Keep the seed corpus in the source tree (committed) and pass it as an
  absolute path so it resolves correctly from the build directory.
- For libFuzzer's regression mode, pass `-runs=0` before the corpus dir.
  Without it, libFuzzer treats the directory as a live corpus and keeps
  fuzzing instead of exiting.
- For the campaign, give libFuzzer two corpus paths — a build-relative
  output dir first (writable; new findings go there) and the seed corpus
  second (treated read-only). Without this split, the campaign writes
  every new-coverage input back into the seed corpus and pollutes the
  source tree.
- Label fuzz tests `["fuzz"]` so users can filter them on or off.
- Pair a fast regression test with a slower campaign — CI on every
  commit runs the regression; nightly CI runs the campaign with a real
  budget (`-max_total_time=600`, `-V 600`, `--run_time 600`).

### Platform notes

- **Linux**: works out of the box with a modern clang.
- **macOS**: Apple's Xcode clang does not ship libFuzzer; install
  Homebrew LLVM (`brew install llvm`) and use that clang. The
  `examples/41_fuzzing/pcons-build.py` script also adds an explicit
  `-L<llvm>/lib/c++` to the link line, because Homebrew's libFuzzer
  archive references libc++ symbols that resolve only against
  Homebrew's libc++ — not Apple's SDK libc++.
- **Windows**: clang-cl supports `-fsanitize=fuzzer,address`, but the
  CRT and ASan DLL setup is more involved than what fits in a small
  example.

---

