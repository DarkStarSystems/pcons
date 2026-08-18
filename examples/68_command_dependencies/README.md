# Commands that build what they need: `depends`

`pcons run <name>` gives a command a **resolved** project, not a **built** one.
A command that needs an artifact on disk says so, and pcons builds it first:

```python
report = project.Program("report", env, ["src/report.c"])


@project.cli_command()
def publish() -> None:
    """Run the program, which pcons has built by now."""
    subprocess.run([str(report.output_nodes[0].path)], check=False)


publish.depends(report)
```

```console
$ rm -rf build                   # nothing generated, nothing built
$ pcons run publish
[1/2] CC obj.report/src/report.c.o
[2/2] LINK report
report: 3 findings
published (exit 0)
```

One invocation wrote the build files, built `report`, and ran the command.

## What the example shows

- **`publish`** declares `report` and needs no existence check and no "run
  `pcons` first" message. If the build fails the command does not run, and
  `pcons run` exits with the build's own code.
- **`inspect-build`** declares nothing, so it starts no build and writes no
  build files. That is still the default, and it has to cope with an artifact
  that may not be there.
- **`release`** is a group that declares a dependency. A verb declares none of
  its own, so the group's cover all of them: `pcons run release notes` builds
  the report too.

## Two things worth knowing

**`depends` takes targets.** The ones the build script already declared -- what
`Program`, `StaticLibrary` and `Command` hand back. Not a name, not a path.

**`pcons run` takes the build flags**, because it can build: `-j/--jobs` and
`--ninja` work as they do on `pcons build`. Spelled before the command name
they configure the build; spelled after it they belong to the command, so
`pcons run -j4 publish` parallelises the build and `pcons run publish -j4`
passes `-j4` to `publish`.

See `examples/65_user_commands` for declaring commands in the first place, and
`docs/user-commands.md` for the full description.
