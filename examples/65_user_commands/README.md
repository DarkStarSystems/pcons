# Commands of your own: `pcons run <name>`

A build script says what to build. The other things a project needs done --
flash a board, publish a release, print where an artifact landed -- usually end
up in shell scripts beside it, each with its own way of finding the build
directory and its own idea of what got built.

This example declares them in the build script instead:

```python
@project.cli_command()
@click.option("--name", default="world", help="Who to greet")
def greet(name: str) -> None:
    """Run the program this build produced."""
    ...
```

```console
$ pcons                          # build first, since `pcons run` never builds
$ pcons run
  greet       Run the program this build produced.
  where       Print where the artifacts landed.
  docs        Documentation tasks.
  check-tool  Fail the way a command is meant to fail.

$ pcons run greet --name pcons
Hello, pcons!

$ pcons run where
build_dir: build
greeter: build/greeter

$ pcons run docs count --unit files
1 files

$ pcons run check-tool
Error: nonesuch-flasher is not on PATH
```

## What the example shows

- **`greet`** takes a click option and runs the program this build produced. A
  command may build or run things, but it has to say so: `pcons run` writes no
  build files and starts no build.
- **`where`** reads `project.build_dir` and a target's `output_nodes`. The
  project is **resolved** by the time a command runs, which is what makes output
  paths available without checking the filesystem.
- **`docs`** is a group. Its subcommands are added with click's own
  `@docs.command()`, on the group pcons handed back, so `pcons run docs list`
  works and pcons never sees the verbs.
- **`check-tool`** fails the way commands are meant to: `raise
  click.ClickException` for a message and exit 1, `ctx.exit(n)` for a code of
  your own. A returned value is ignored.

## Two things worth knowing

**The listing comes from the build directory.** `pcons run` and `pcons run
--help` read the names from `build/pcons_cache.json` rather than running the
build script, so listing costs nothing -- and a newly declared command appears
only after the next `pcons generate`. Running one by name does read the script,
so `pcons run greet --help` always shows the current options.

**click is part of the surface.** The decorators return real `click.Command` and
`click.Group` objects, so `click.option`, `click.argument` and `click.Choice`
work as they do anywhere else. An add-on module can declare commands too, with
`pcons.cli_command()` -- no project needed, and no build script needed to run
one.

See `docs/user-commands.md` for the full description.
