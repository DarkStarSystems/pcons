# A generator that has to run from the source root

Build tools run from the build directory, and pcons writes every path in a
command relative to it. Some generators can't live with that: this one looks
up its input at the fixed relative path `data/items.txt` and takes only the
output file on its command line. Anything ported from a build system that ran
from the top of the tree is full of tools like it.

`cwd=` moves the command, and moves its paths with it:

```python
make_items = project.Program("make-items", env, sources=["src/make-items.c"])

env.Command(
    target=gen_dir / "items.c",
    source=[make_items],
    depends=["data/items.txt"],  # read by the tool, not named on its command line
    command="$SOURCE $TARGET",
    cwd=project.root_dir,
    write_if_different=True,
)
```

`$SOURCE` and `$TARGET` come out relative to the working directory the command
asked for, so nothing else in the rule changes:

```
rule command_4414982a
  command = ... stable_output --pre $out && cd .. && $source_0 $target_0 \
            && cd build && ... stable_output --post $out
```

The paths stay relative, so `build.ninja` is as relocatable as it was.

**Don't write the `cd` yourself.** `command="cd $SRCDIR && ..."` looks
equivalent and is not: the `write_if_different` wrapper around your command is
two halves that have to run in the same directory, and a one-way `cd` strands
the second one. It would find none of the outputs the first half stashed,
restore nothing, and exit 0 — every downstream target rebuilding on every run,
with nothing in the output to say why. `cwd=` changes back; a bare `cd` now
fails the build instead of quietly costing you a relink.

**Why the rebuild matters here.** The tool rewrites its output every run, so
touching the data file re-runs it — but the bytes are identical, so
`write_if_different` restores the file (timestamp included) and nothing
downstream rebuilds:

```
$ touch data/items.txt
$ ninja -C build
[1/4] COMMAND gen/items.c
make-items: wrote 3 items to build/gen/items.c
```

One step, no recompile, no relink. `test.toml` asserts exactly that.
