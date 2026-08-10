# Command-line reference

```
pcons [options] [KEY=value ...] [target ...]
pcons <command> [options] [KEY=value ...] [target ...]
```

With no command, pcons generates build files and then builds. This page covers
what the CLI accepts; the [user guide](user-guide.md) covers what the features
do.

## Commands

### `pcons`

Generate build files if they are missing or out of date, then build. Positional
arguments are targets to build, or `KEY=value` build variables.

```bash
pcons                     # generate and build the default targets
pcons myapp               # build one target
pcons CC=clang myapp      # set a build variable, then build
```

### `pcons generate`

Generate build files without building.

| Option | |
|---|---|
| `--graph [FILE]` | Write the dependency graph as DOT (default: stdout) |
| `--mermaid [FILE]` | Write the dependency graph as Mermaid (default: stdout) |

### `pcons build`

Build with the tool that matches the generated files (ninja, make or
xcodebuild), regenerating them first if they're stale.

| Option | |
|---|---|
| `-j N`, `--jobs N` | Parallel build jobs |
| `--ninja PROG` | Ninja-compatible runner to invoke, e.g. `n2`. Defaults to `$NINJA`, then `ninja` |
| `--watch` | Build, then rebuild whenever a watched file changes; Ctrl-C to stop. See [Watching for changes](user-guide.md#watching-for-changes) |

### `pcons clean`

Remove build artifacts.

| Option | |
|---|---|
| `-a`, `--all` | Remove the entire build directory, not just its outputs |

### `pcons info`

Show the build script's documentation and the variables it reads.

| Option | |
|---|---|
| `-t`, `--targets` | List every target (runs the build script) |

### `pcons init`

Write a `pcons-build.py` for the current directory. It adopts any C or C++
sources it finds; if there are none, it scaffolds a hello-world program.

| Option | |
|---|---|
| `-f`, `--force` | Overwrite an existing `pcons-build.py` |
| `--lang {c,cpp}` | Language for the starter program when no sources are found (default: `cpp`) |

### `pcons cache`

Inspect or clear the per-build-directory cache of settings chosen on the
command line. See [Persistent configuration
cache](user-guide.md#persistent-configuration-cache).

```bash
pcons cache list     # what is persisted
pcons cache show     # the whole cache
pcons cache clear    # discard it
pcons cache path     # where it lives
```

### `pcons test`

Run the tests declared by `project.Test()`. This subcommand takes the test
runner's own options (`-L`, `-R`, `-E`, `--junit` and so on), not the ones
below; see [Testing](testing.md).

Everything after `test` reaches the runner untouched, apart from `-C DIR`.
Write `pcons test -- -C DIR` to hand `-C` to the runner instead: the first
`--` is consumed, and any further one is passed on.

## Options

Accepted by every command unless noted:

| Option | |
|---|---|
| `-h`, `--help` | Show help and exit |
| `--version` | Show the version and exit |
| `-C DIR`, `--directory DIR` | Change to *DIR* first, before anything else is parsed |
| `-B DIR`, `--build-dir DIR` | Build directory. Default: `$PCONS_BUILD_DIR`, else `build` |
| `-b FILE`, `--build-script FILE` | Path to the build script. Default: `pcons-build.py` in the current directory |
| `-v`, `--verbose` | Verbose output |
| `--debug SUBSYSTEMS` | Trace named subsystems, comma-separated: `configure`, `resolve`, `generate`, `subst`, `env`, `deps`; or `all`, or `help` to list them |
| `--variant NAME` | Build variant, e.g. `debug`, `release` |
| `-G NAME`, `--generator NAME` | Generator: `ninja` (default), `make`, `xcode`, `metadata`. Repeatable |
| `--reconfigure` | Re-run configure checks instead of using cached results |
| `--fresh` | Discard the persisted cache before this run, like `cmake --fresh` |
| `--modules-path PATHS` | Extra directories to search for pcons add-on modules, separated by `:` (`;` on Windows) |

## Build variables

Any `KEY=value` argument becomes a build variable your script can read with
`get_var()`. It's remembered per build directory, so later runs pick it up:

```bash
pcons PORT=ofx USE_CUDA=1 PREFIX=/usr/local
```

See [Build variables](user-guide.md#build-variables) for how a script reads
them and how their types are decided.

## Environment variables

Read by pcons:

| Variable | |
|---|---|
| `PCONS_BUILD_DIR` | Default build directory, as if `-B` had been given |
| `PCONS_VARS` | Build variables as a JSON object, as if given as `KEY=value` |
| `PCONS_VARIANT` | Default variant, as if `--variant` had been given |
| `PCONS_GENERATOR` | Default generator; several may be joined with `:` |
| `PCONS_MODULES_PATH` | Extra add-on module directories, as if `--modules-path` |
| `PCONS_DEBUG` | Subsystems to trace, as if `--debug` |
| `NINJA` | Ninja-compatible runner to invoke, as if `--ninja` |
| `CC`, `CXX`, `FC`, `AR`, `RC`, `SWIFTC`, `CUDACXX` | Authoritative choice of that tool. A value that cannot be found is an error, never a fall-through to detection |

Read by a build script, through `get_var()`:

| Variable | |
|---|---|
| `PCONS_INSTALL_PREFIX` | Prefix for install targets. Default: `<project>/dist` |
| `PCONS_WARN_BUILD_DIR_PATHS` | Set false to silence the warning about build-directory-relative paths in commands |

Set by pcons for the build script and the commands it runs:

| Variable | |
|---|---|
| `PCONS_SOURCE_DIR` | Absolute path of the directory holding the build script |
| `PCONS_BUILD_DIR` | Absolute path of the build directory |

Read by a persistent worker and its client:

| Variable | |
|---|---|
| `PCONS_WORKER_IDLE_TIMEOUT` | Seconds a worker waits for work before exiting |
| `PCONS_WORKER_DEBUG` | Set to report why a worker was not used, and keep its stderr |

## Exit status

`0` on success, non-zero otherwise: the build script failed, the build tool
failed, or pcons couldn't parse the arguments. Under `--watch` a failed build
leaves the watch running; only Ctrl-C ends the session, and that exits `0`.
