# A generator you built, run over however many inputs there are

This is the ordinary shape of a code-generation rule, and it depends on three
things pcons has to get right:

```python
collate = project.Program("collate", env, sources=["src/collate.c"])

def_dir = project.root_dir / "defs"
project.add_configure_dependency(def_dir)
def_files = sorted(p.relative_to(project.root_dir) for p in def_dir.glob("*.def"))

env.Command(
    target=gen_dir / "entries.c",
    source=[collate, *def_files],
    command="${SOURCES[0]} $TARGET ${SOURCES[1:]}",
    write_if_different=True,
)
```

**Declared order is preserved.** `${SOURCES[0]}` is `collate` because `collate`
was written first. It doesn't matter that it's a Target and the rest are paths;
pcons used to append Targets last, which made `${SOURCES[0]}` a `.def` file and
the build tried to execute it.

**Slices.** How many `.def` files there are is a property of the project, not
of this rule, so the command says "the rest of them" — `${SOURCES[1:]}` — and
adding a file changes nothing here. `${SOURCES[n]}`, `${SOURCES[n:m]}` and
`${SOURCES[:m]}` all work; anything else inside `${...}` is an error rather
than a literal passed through to the build tool.

**A glob is a question asked at configure time.** Declaring the directory it
read as a configure dependency is what makes a new `.def` file take effect:

```
$ printf 'triangle\n' > defs/more.def
$ ninja -C build
[0/1] Regenerating build.ninja      # the directory changed, so pcons re-ran
[1/5] COMMAND gen/entries.c
...
5 entries
```

Without that line the glob is stale until something else happens to re-run
pcons — the file sits there and nothing notices.
