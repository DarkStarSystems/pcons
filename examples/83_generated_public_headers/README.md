# A library whose public header is generated

`metrics` keeps its public header in `build/gen/`, written by
`gen_limits.py`. The library says so once:

```python
metrics.depends(limits)
metrics.public.include_dirs.append(gen_dir)
```

`app` links `metrics` and includes `metrics_limits.h`. It says nothing about
the generator. It does not have to: the include directory is a usage
requirement of `metrics`, so the ordering that fills it is one too, and every
target that links `metrics` waits for the generator.

```
$ pcons
[1/5] COMMAND gen/metrics_limits.h
[2/5] CC obj.metrics/src/metrics.c.o
[3/5] AR libmetrics.a
[4/5] CC obj.app/app/main.c.o
[5/5] LINK app
$ ninja -C build
ninja: no work to do.
$ ./build/app
generated 8 8
```

## Why the generator sleeps

`gen_limits.py` sleeps for a second and writes its output through a rename.

Both are deliberate. Without the sleep an unordered generator still wins the
race often enough that a broken build looks correct. The rename is how real
code generators write, and it moves the *directory's* timestamp on every run.

That second point is what makes this example a convergence test rather than a
correctness test. A build-time scanner that lists a directory records the
directory's timestamp among its inputs, which is how it notices a header
appearing there later. Qt's automoc does exactly that with every directory on
its include path. If the scan runs while the generator is still writing, the
directory ends up newer than the scan, and the next build re-runs the scan,
rewrites its output, and a third build recompiles and relinks. The artifacts
are correct at every step and the tree still never settles.

`[[rebuild]] expect_no_work = true` in `test.toml` is the check: build twice
from clean, and the second build has to do nothing.
