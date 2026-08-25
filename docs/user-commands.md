# Custom CLI commands: `pcons run`

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

The command contains a lexical closure of local variables, so  `flash` can access `firmware` and `project` by lexical
scope — so it knows the target's output path, the build directory and other variables .

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

!!! note
    `pcons run` and `pcons run --help` read the names from the build directory's cache, so listing never runs the build script.

    - A newly declared command is listed only **after the next `pcons generate`**
      (or any build, which generates). Running it by name works either way.
    - `pcons run <name> --help` does read the script, because only the script knows
      the options.
    - `pcons cache clear` drops the listing along with everything else the build
      directory persisted, so `pcons run` lists nothing again until the next
      generate. Running a command by name is unaffected.
    - `pcons --help` before the first build shows `run` itself, because it doesn't yet know the details of your commands.


## Completion

With completion installed — `source <(_PCONS_COMPLETE=zsh_source pcons)`, or `bash_source` for
bash — `pcons run <TAB>` offers the script's declared names with their help, as well as the usual completion targets. It comes from the
build directory's cache and runs nothing (for speed). So an add-on's commands may be listed by `pcons run` but are not offered on TAB.
Loading a module means executing it, and anything it printed would land in the middle of the
completion protocol and be read back as candidate names.

It can't offer a command's own options (`pcons run flash --<TAB>`) or a group's verbs (`pcons run docs <TAB>`): both need the real command object, which only the build script has, and running a build script on a keystroke would mean configure checks between two presses of TAB.

## What a custom CLI command gets

By the time the callback runs:

- the build script has been read, and the **project is resolved**, so
  `target.output_nodes` and `project.build_dir` are populated;
- `pcons.get_var()` and `pcons.get_variant()` are populated as they are in the script
  body, because the command runs inside the script's live environment;
- **no build files have been written and no build has run**, unless the command
  declares a dependency, in which case the command is run after that build completes. See below.

## Building first

A command that needs one or more targets names the targets it needs:

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
command. If the build fails, the command does not run, and `pcons run` exits with
the build's own code. It all happens in one reading of the build script: the
decision to generate is taken after the script has been read, once pcons knows
what the command asked for.

!!! note
    `depends` takes targets, i.e. `Program`, `StaticLibrary`, `Command` results, not paths or strings. A string or a path would have to be
    resolved against a project.

A group can use `depends` too, and what it declares is built before any of its
verbs:

```python
@project.cli_group()
def release() -> None:
    """Release tasks."""


release.depends(firmware)
```

Since `pcons run` can build, it takes the flags that govern building: `-j` /
`--jobs` and `--ninja`, as `pcons build` does. Spelled before the command name
they configure the build, spelled after it they belong to the command.

## Groups

`cli_group` declares a group. Add commands to it with click's own decorator, on the
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

The commands belong to the group, so they never collide with a top-level command
name. One consequence of the cached listing: only the
group's own name and help are cached, so `pcons run docs --help` has to run the build
script to find its verbs.

A verb declares its own dependencies, and the group's apply to every verb:

```python
@project.cli_group()
def release() -> None:
    """Release tasks."""


release.depends(firmware)


@release.command("notes")
def release_notes() -> None:
    """Write the release notes."""
    ...


release_notes.depends(changelog)
```

`pcons run release notes` builds `firmware`, because the group asked for it, then
`changelog`, because the verb did. A target both name is built once. A subgroup
added with `@release.group()` works the same way, at any depth.

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
module-level form from its `register()`:

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
`click.Path`, `click.pass_context` and the rest work as they do anywhere else. Names come from click too: `def build_docs`
becomes `build-docs`.

A custom command option is private to that command. `pcons run` declares `-B/--build-dir` for itself, and a
command sees it only if it declares it as well -- the callback reaches the build
directory through the project instead. `pcons run` takes no `KEY=value` build
variables either; a command that wants them should declare its own argument, or read
`pcons.get_var()`.

## Example

`examples/65_user_commands` is a working project with an option, a group, a
command reading the resolved project, and a command that fails properly.
