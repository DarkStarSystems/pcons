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
pcons -- -myapp           # build a target whose name starts with a dash
```

`--` marks everything after it as a target or a build variable, never an
option. A bare `-` is not a target unless written that way. A command name is
not protected by it: `pcons -- build` still runs the `build` command, since no
command name starts with a dash.

### `pcons generate`

Generate build files without building.

| Option | |
|---|---|
| `--graph [FILE]` | Write the dependency graph as DOT (default: stdout) |
| `--mermaid [FILE]` | Write the dependency graph as Mermaid (default: stdout) |

### `pcons build`

Build with the tool that matches the generated files (ninja, make or
xcodebuild), regenerating them first if they're stale. Unlike a bare `pcons`,
this needs no `pcons-build.py`: with nothing to regenerate from, it builds
whatever files are already there. Unusual corner case but OK.

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

### `pcons explain`

Show how each target's commands are constructed, and where every piece came
from. Each target section lists its concrete commands, its effective usage
requirements with the target that contributed each one (`include_dirs`,
`defines`, `link_libs`, ...), and the environment it builds with; each
environment section attributes every flag and define to the preset, variant
or toolchain that set it (the CLI face of `env.explain()`). Runs the build
script but writes no build files and persists nothing.

Commands are spelled exactly as the build runs them — from the build
directory — so with `--width 0` they can be pasted into a shell there and
re-run or hand-edited.

Arguments are targets to explain and/or build variables (`KEY=value`); with
no targets, every target is explained.

| Option | |
|---|---|
| `--color {auto,always,never}` | Colorize the report (default `auto`: only on a terminal) |
| `--width COLS` | Truncate command lines to COLS columns; `0` for unlimited (default: terminal width, unlimited when piped) |

```console
$ pcons explain simulator --variant debug
## Explanation of Targets and Environments: ~/src/myproject
Commands are shown as the build runs them, from the build directory (build).

=== simulator  (program)  [env #1]  pcons-build.py:23
  * build/obj.simulator/main.o  <-  main.c
      /usr/bin/clang -Wall -O0 -g -I../include -MD -MF obj.simulator/main.o.d -c -o obj.simulator/main.o ../main.c
  * build/simulator  <-  build/obj.simulator/main.o
      /usr/bin/clang -o simulator obj.simulator/main.o -lm
  requirements:
    include_dirs:
      include  <- math (public)
    link_libs:
      physics  <- simulator (private)
      m        <- math (public)
      math     <- physics (public)

Environment #1  (toolchain: llvm)  pcons-build.py:8
  cc.flags:
    -Wall  <- warnings (feature)
    -O0    <- debug (variant)
    -g     <- debug (variant)
```

A node compiled with a per-source environment override, or a dependency
built in a different environment, is annotated with that environment's
label (`[env #2]`).

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
pcons cache          # same as `pcons cache list`
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

### `pcons completion`

Set up tab completion for bash, zsh or fish. The script is generated from the
command tree, so it completes command names, option names and the values of
options with a fixed set, such as `-G`.

```bash
pcons completion install          # write it and wire it up, for $SHELL
pcons completion install zsh      # for a shell you name
pcons completion install -y zsh   # without confirming first
pcons completion uninstall zsh    # undo both edits
pcons completion show zsh         # print the script, write nothing
```

`install` says which files it will write and which lines it will add, then asks,
unless `-y` is given. It writes:

| Shell | Script | Startup file |
|---|---|---|
| bash | `~/.bash_completions/pcons.sh` | one `source` line in `~/.bashrc` |
| zsh | `~/.zfunc/_pcons` | an `fpath` and a `compinit` line in `~/.zshrc` |
| fish | `~/.config/fish/completions/pcons.fish` | none, fish reads that directory itself |

The startup lines go in one delimited block, so installing twice changes nothing
and `uninstall` removes what was added and leaves the rest of the file alone.
Completion takes effect in the next shell.

With no shell named, `$SHELL` decides. It is never guessed: with `$SHELL` unset,
or naming a shell click writes no script for, the command fails and says so.

To evaluate the script instead of installing it, put this in your startup file:

```bash
eval "$(pcons completion show bash)"      # or zsh
pcons completion show fish | source       # fish
```

That runs pcons on every shell start, which costs about 95 ms. Installing does
not.

PowerShell is not supported: click, which generates the script, has no
PowerShell completion class.

Target names are not completed. `pcons hel<TAB>` would have to run the build
script to know that `hello` exists.

## Options

Every option below may be written before the command or after it. Spelled on
both sides, the later one wins.

Accepted by every command:

| Option | |
|---|---|
| `-C DIR`, `--directory DIR` | Change to *DIR* first, before anything else is parsed |

Accepted by every command except `test`, which hands everything after it to the
test runner, and `completion`, which reads nothing from the project:

| Option | |
|---|---|
| `-h`, `--help` | Show help and exit |
| `-B DIR`, `--build-dir DIR` | Build directory. Default: `$PCONS_BUILD_DIR`, else `build` |
| `-v`, `--verbose` | Verbose output |
| `--debug SUBSYSTEMS` | Trace named subsystems, comma-separated: `configure`, `resolve`, `generate`, `subst`, `env`, `deps`; or `all`, or `help` to list them |
| `--modules-path PATHS` | Extra directories to search for pcons add-on modules, separated by `:` (`;` on Windows) |

Accepted by the commands that run the build script, which are `pcons`,
`generate`, `build` and `info`:

| Option | |
|---|---|
| `-b FILE`, `--build-script FILE` | Path to the build script. Default: `pcons-build.py` in the current directory |
| `--variant NAME` | Build variant, e.g. `debug`, `release` |
| `-G NAME`, `--generator NAME` | Generator: `ninja` (default), `make`, `xcode`, `metadata`. Repeatable |
| `--reconfigure` | Re-run configure checks instead of using cached results |
| `--fresh` | Discard the persisted cache before this run, like `cmake --fresh` |

`--version` prints the version and exits. It belongs to `pcons` itself, not to
its commands.

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
