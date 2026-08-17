# Using pcons as a library

`pcons` the command is one client of the machinery underneath. A larger
program can use that machinery directly: a build step inside an application,
a release pipeline, a package recipe, or even a custom CLI with its own options.
Note: this is currrently still a work in progress.

Here are the two basic ways to use pcons as a library:

## An embedded build step

Describe the build in your own program and ask for the build files:

```python
from pcons import Project, find_c_toolchain

project = Project("embedded", root_dir=here, build_dir="build-embed")
env = project.Environment(toolchain=find_c_toolchain())
project.Program("hello", env, sources=["src/hello.c"])
project.write_build_files()
# then run ninja yourself, e.g. pcons.cli.run_ninja(here / "build-embed") 
# or use subprocess
```

`write_build_files()` writes the project's build files directly,
resolving first as needed. Under `pcons` the same call is what happens
anyway, so a script that calls it works both ways.

Two things are different from a CLI run:

  **No default regen rule.** Generated build files normally carry a
  self-regeneration edge that re-runs pcons when the build description
  changes. An embedded run writes none: `sys.argv` is the *driver*
  program, and re-running that as a build step is likely not right. A
  specified regen command is used verbatim and runs from the build
  directory:

  ```python
  project.write_build_files(regen_command=["python3", "../driver.py"])
  ```

With several top-level projects, call it on each; every project has its own
build directory and build files, exactly as under `pcons`.

## A custom CLI

To wrap an ordinary `pcons-build.py` in your own CLI, use the CLI's
own service layer:

```python
from pcons.cli import run_ninja, run_script

code, projects = run_script(root / "pcons-build.py", root / "build")
if code == 0 and projects:
    code = run_ninja(root / "build")
```

`run_script()` is what `pcons` itself runs: it sets up the `PCONS_*`
environment, applies settings precedence (command line over the per-build-dir
cache), records the invocation for the regen rule, manages cwd and
`sys.path`, runs the script, writes every project's build files, and cleans
up. It returns the exit code and the projects the script created.

## The environment contract

A build script reads its configuration from `PCONS_*` environment variables;
`run_script()` sets them from its arguments. If you run a build script some
other way, set them yourself:

| Variable | Read by |
|---|---|
| `PCONS_BUILD_DIR` | `Project()` default build directory |
| `PCONS_SOURCE_DIR` | `Project()` default root directory |
| `PCONS_VARS` | `get_var()`, as a JSON object |
| `PCONS_VARIANT` | `get_variant()` |
| `PCONS_GENERATOR` | which generators `write_build_files()` runs |

## 🚧 Work In Progress

This is not yet a full API; that's being designed in
[#90](https://github.com/DarkStarSystems/pcons/issues/90). Until then,
`write_build_files()` and `run_script()` are the stable interface, and
`examples/67_embedded_build` exercises both styles in CI.
