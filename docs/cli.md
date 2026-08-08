# Command-line reference

## pcons generate

Generate Ninja build files without building:

```bash
pcons generate                     # Generate build.ninja
pcons generate --variant=debug     # Generate for debug build
pcons generate CC=clang CXX=clang++  # Pass variables
```

## pcons build

Build targets using Ninja:

```bash
pcons build              # Build all default targets
pcons build myapp        # Build specific target
pcons build -j8          # Use 8 parallel jobs
pcons build --verbose    # Show commands being run
```

## Watching for changes

`--watch` builds once and then rebuilds whenever anything in the source tree
changes. It works with the default command and with `pcons build`, and takes
the same targets and options as a normal build:

```bash
pcons --watch            # Build, then rebuild on every change
pcons --watch myapp      # Watch, building only 'myapp'
pcons build --watch -j8
```

Editing the build script counts as a change: ninja re-runs pcons to bring
`build.ninja` up to date before building, so adding a source file or changing a
flag takes effect without restarting the watch. A build that fails does not
stop the watch — the next edit is usually the fix. Press Ctrl-C to stop.

The build directory is never watched (reacting to the build's own output would
loop forever), nor are VCS directories, virtualenvs, tool caches, or editor
scratch files. Anything ninja knows how to build is also left out, wherever it
lands — so a command that generates a file next to its sources, or an in-source
build (`-B .`), does not retrigger the build that wrote it.

Two things a watch reports that an ordinary build does not:

- **A build that did not converge.** If a command never creates the output it
  declares, ninja reruns it on every build and says nothing. After each
  successful build pcons asks ninja whether work remains, and passes on its
  answer:

  ```
  WARNING: the build did not converge: ninja still has work to do right after a
  successful build ... Ninja explains:
  WARNING:     output declared.txt doesn't exist
  ```

- **A rebuild loop.** If several builds in a row are triggered the instant the
  previous one finished, all by the same file, the watch stops and names it —
  that file is written by the build itself, so each build is asking for the
  next. Declare it as an output of the command that writes it, or send it to the
  build directory.

Watching uses the platform's native filesystem notification (inotify, FSEvents,
ReadDirectoryChangesW) through the
[watchfiles](https://pypi.org/project/watchfiles/) package. It installs with
pcons on Linux, macOS and Windows, so `--watch` works out of the box — including
with `uvx pcons --watch`. On any other platform pcons installs without it and
`--watch` says so; ask for it explicitly with `pip install 'pcons[watch]'`,
which builds from source and needs a Rust toolchain.

## pcons (default)

Running `pcons` without a subcommand does both generate and build:

```bash
pcons                    # Generate + Build
pcons --variant=debug    # Generate + Build with variant
pcons FOO=bar            # Pass variables
```

## pcons clean

Clean build artifacts:

```bash
pcons clean        # Run ninja -t clean
pcons clean --all  # Remove entire build directory
```

## Command-Line Options

| Option | Description |
|--------|-------------|
| `--variant=NAME` or `-v NAME` | Set build variant (debug, release) |
| `-B DIR` or `--build-dir=DIR` | Set build directory (default: build) |
| `-C` or `--reconfigure` | Force re-run configuration |
| `--fresh` | Discard the persisted cache and start clean |
| `-j N` or `--jobs=N` | Number of parallel build jobs |
| `--watch` | Rebuild whenever a watched file changes (needs `pcons[watch]`) |
| `--verbose` | Show verbose output |
| `--debug` | Show debug output |
| `KEY=value` | Pass build variables |

## Build Variables

Pass variables to your build script:

```bash
pcons PORT=ofx USE_CUDA=1 PREFIX=/usr/local
```

Access them in `pcons-build.py`:

```python
from pathlib import Path

from pcons import get_var

port = get_var("PORT", "ofx")
use_cuda = get_var("USE_CUDA", False)
prefix = get_var("PREFIX", Path("/usr/local"))
```

### Typed Variables

The default's type selects the conversion, so a variable never has to be parsed
by hand:

```python
use_cuda = get_var("USE_CUDA", False)  # bool
opt_level = get_var("OPT_LEVEL", 2)  # int
scale = get_var("SCALE", 1.0)  # float
port = get_var("PORT", "ofx")  # str
prefix = get_var("PREFIX", Path("/usr/local"))  # Path
```

Pass `type=` when there is no default. The result is `None` when the variable is
unset, which is falsy, so it still reads well in a condition:

```python
if get_var("BUILD_TESTS", type=bool):
    ...
```

A default and a `type=` together raise: the default already picks the
conversion, so the pair is either redundant or a contradiction.

Booleans accept `1`, `on`, `yes`, `true`, `y` and `0`, `off`, `no`, `false`, `n`,
case-insensitive. Any other value raises `ConfigureError` instead of silently
reading as false, so `USE_CUDA=enabled` is reported rather than ignored. `int`
and `float` raise the same way on a value they cannot parse.

A `Path` is taken verbatim, never resolved, so `PREFIX=dist` stays relative and
you decide what it is relative to. An empty value is an error rather than
`Path(".")`.

The default itself is never parsed, it is returned as-is when the variable is
unset. With no default and no `type=`, `get_var` returns the raw string or
`None`.

## Persistent Configuration Cache

Settings you choose on the command line persist per build directory, like
CMake's `CMakeCache.txt`. Configure once, then run bare:

```bash
pcons generate PORT=ofx --variant=debug -G ninja   # choose settings
pcons                                               # reuses PORT, variant, generator
```

What persists: build variables, the variant, and the generator. They are stored
in `<build_dir>/pcons_cache.json` and written only after a successful run.

Precedence, highest to lowest:

1. This run's command line (`PORT=x`, `--variant`, `-G`)
2. Environment: `PORT=x pcons`, `VARIANT`, `GENERATOR`, and the `PCONS_VARS` /
   `PCONS_VARIANT` / `PCONS_GENERATOR` forms
3. Persisted cache from a prior run
4. The `default` passed to `get_var` / `get_variant`

An environment value overrides the cache but is not written to it, so exporting
one steers a run without changing what a later bare run reuses.

The cache is tied to `$PCONS_BUILD_DIR`, which `pcons` always sets (and `-B`
overrides). Running the script directly with `python pcons-build.py` uses no
cache, so the same environment produces the same build either way.

Inspect and reset:

```bash
pcons cache list      # show persisted vars, variant, generator
pcons cache show      # same, plus the cache file path and source dir
pcons cache path      # print the cache file path
pcons cache clear     # empty the cache
pcons generate --fresh PORT=y   # ignore the old cache, start clean
```

Change settings through these commands, not by editing `pcons_cache.json`. The
file is not a regeneration input, so a hand-edit is not picked up automatically,
and the self-regeneration command pins the values it was generated with, so a
manual change would be overwritten on the next run anyway.

Two guards catch stale caches:

- A variable that was persisted but the build script never reads is reported
  (`pcons FEATRUE=on` typo, or a setting you dropped).
- A cache whose recorded source directory no longer matches (a copied or moved
  build dir) is ignored with a warning and rebuilt for the current tree.

There is no API to read or write the cache from a build script; it holds only
the settings above. If you need structured configuration, write a Python config
file and import it from `pcons-build.py`.

---

