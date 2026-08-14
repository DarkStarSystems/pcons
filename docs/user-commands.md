# Commands of your own

A build script can declare commands that extend pcons' CLI. They run as
`pcons run <name>`:

```python
import subprocess

import click

from pcons import Project

project = Project("app")
env = project.Environment(toolchain="c")
firmware = project.Program("firmware", env, sources=["src/main.c"])


@project.cli_command()
@click.option("--baud", default=115200, help="Serial speed")
def flash(baud: int) -> None:
    """Flash the board."""
    image = firmware.output_nodes[0].path
    subprocess.run(["esptool", "--baud", str(baud), "write_flash", "0x0", str(image)])
```

```console
$ pcons run flash --baud 9600
```

The point is the closure. `flash` reaches `firmware` and `project` by lexical
scope, so it knows the target's output path, the build directory and every
variable the script read, with none of it passed in or rediscovered.

## Listing what is available

```console
$ pcons run
  flash  Flash the board.
  docs   Documentation tasks.

$ pcons run flash --help
Usage: pcons run flash [OPTIONS]

  Flash the board.

Options:
  --baud INTEGER  Serial speed
  -h, --help      Show this message and exit.
```

`pcons run` and `pcons run --help` read the names from the build directory's
cache, so listing never runs the build script. Two consequences:

- A newly declared command is listed only **after the next `pcons generate`**
  (or any build, which generates). Before that it is simply absent, with no
  notice -- bare `pcons run` says what to do only when the build directory
  knows of no commands at all. Running it by name works either way.
- `pcons run <name> --help` does read the script, because only the script knows
  the options.
- `pcons cache clear` drops the listing along with everything else the build
  directory persisted, so `pcons run` lists nothing again until the next
  generate. Running a command by name is unaffected.

`pcons --help` shows `run` itself, and nothing about your commands.

## Completion

With completion installed -- `source <(_PCONS_COMPLETE=zsh_source pcons)`, or `bash_source` for
bash -- `pcons run <TAB>` offers the script's declared names with their help. It comes from the
build directory's cache and runs nothing: not the build script, and not an add-on module's
`register()` either. So an add-on's commands are listed by `pcons run` but are not offered on TAB.
Loading a module means executing it, and anything it printed would land in the middle of the
completion protocol and be read back as candidate names.

What it cannot offer is a command's own options (`pcons run flash --<TAB>`) or a group's verbs
(`pcons run docs <TAB>`): both need the real command object, which only the build script has, and
running a build script on a keystroke would mean configure checks between two presses of TAB.

## What a command gets

By the time the callback runs:

- the build script has been read, and the **project is resolved**, so
  `target.output_nodes` and `project.build_dir` are populated;
- `pcons.get_var()` and `pcons.get_variant()` answer as they do in the script
  body, because the command runs inside the script's live environment;
- **no build files have been written and no build has run**, unless the command
  declared a dependency. `pcons run` is not a general way to build.

## Building first

A command that needs an artifact on disk names the targets it needs:

```python
firmware = project.Program("firmware", env, ["src/main.c"])


@project.cli_command()
def package() -> None:
    """Package the built program."""
    image = Path(str(firmware.output_nodes[0].path))
    ...


package.depends(firmware)
```

`pcons run package` then writes the build files, builds `firmware`, and runs the
command. If the build fails the command does not run, and `pcons run` exits with
the build's own code. It all happens in one reading of the build script: the
decision to generate is taken after the script has been read, once pcons knows
what the command asked for.

`depends` takes targets -- what `Program`, `StaticLibrary`, `Command` and the
rest hand back -- and takes nothing else. A name or a path would have to be
resolved against a project, and the command registry deliberately holds none.

A group declares them for all its verbs at once; a verb declares none of its
own:

```python
@project.cli_group()
def release() -> None:
    """Release tasks."""


release.depends(firmware)
```

Since `pcons run` can build, it takes the flags that govern building: `-j` /
`--jobs` and `--ninja`, as `pcons build` does. Spelled before the command name
they configure the build, spelled after it they belong to the command.

**Declaring a dependency is the opt-in.** A command that declares none writes no
build files and starts no build, so it has to cope with an artifact that may not
exist:

```python
@project.cli_command()
def inspect_image() -> None:
    """Report on the image, if there is one."""
    image = Path(str(firmware.output_nodes[0].path))
    if not image.exists():
        raise click.ClickException(f"{image} is not built yet. Run `pcons` first.")
```

## Groups

`cli_group` declares a group. Add verbs to it with click's own decorator, on the
group pcons hands back:

```python
@project.cli_group()
def docs() -> None:
    """Documentation tasks."""


@docs.command("list")
def docs_list() -> None:
    """List the source files."""
    ...
```

```console
$ pcons run docs list
```

The verbs belong to the group, so they never collide with a top-level command
name and pcons never sees them. One consequence of the cached listing: only the
group's own name and help are cached, so `pcons run docs --help` runs the build
script to find its verbs.

## Failing

click's conventions are pcons' conventions here:

```python
raise click.ClickException("no device found")  # message, exit 1
ctx.exit(3)  # exit code of your own
```

with `ctx` from `@click.pass_context`. A returned value is ignored, exactly as
for pcons' own commands.

## Declaring from an add-on module

An [add-on module](user-guide.md#add-on-modules) has no project, so it uses the
module-level spelling from its `register()`:

```python
# ~/.pcons/modules/deploy.py
import pcons

__pcons_module__ = {"name": "deploy", "version": "1.0"}


def register() -> None:
    @pcons.cli_command()
    @click.option("--host", required=True)
    def deploy(host: str) -> None:
        """Copy the release to a host."""
        ...
```

`pcons.cli_command()` and `project.cli_command()` are the same registry;
`project.cli_command()` is sugar for a build script that has `project` in hand.

A module command needs **no build script at all**: with no `pcons-build.py`
present it runs on its own, with no project. With one, it runs inside the
script's environment like any other command and does see a resolved project.

A name declared by both a module and the build script is an **error**, and
neither runs:

```console
$ pcons run flash
Error: CLI command 'flash' is declared by more than one origin (module:deploy,
script). Rename one of them.
```

The clash is reported when the name is used, not when it is declared, so a
third-party module can never fail a build script that does not mention it. Every
other name keeps working, and generating is unaffected.

## click is part of the surface

The decorators return real `click.Command` and `click.Group` objects. There is no
translation layer, so `click.option`, `click.argument`, `click.Choice`,
`click.Path`, `click.pass_context` and the rest work as they do anywhere else,
and pcons has no opinion to learn. Names come from click too: `def build_docs`
becomes `build-docs`.

An option is yours alone. `pcons run` declares `-B/--build-dir` for itself, and a
command sees it only if it declares it as well -- the callback reaches the build
directory through the project instead. `pcons run` takes no `KEY=value` build
variables either; a command that wants them declares its own argument, or reads
`pcons.get_var()`.

## Example

`examples/65_user_commands` is a working project with an option, a group, a
command reading the resolved project, and a command that fails properly.
