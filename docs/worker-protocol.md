# The worker protocol

pcons does not implement workers. It defines what one has to do, and runs a
client in front of each action that uses one. This document is that contract:
what a worker must do, and what pcons guarantees in return. For what workers
are *for* and how a build script declares one, see [Persistent
Workers](user-guide.md#persistent-workers) in the user guide.

Anything that can listen on a Unix socket can be a worker: a Python process, a
compiled binary, a thin client for a service that is already running.
`pcons/workers/python_server.py` implements everything below in about 150 lines
of standard library, and is worth reading beside this page.

## How a worker is reached

A build script declares how to start one:

```python
env.Command(
    target="report.pdf",
    source="report.py",
    command="python $SOURCE --out $TARGET",
    worker=Worker(command=["my-worker", "--profile=render"]),
)
```

pcons renders that into the action's command line as a client invocation. At
build time the client:

1. Connects to the worker's socket, if something is listening.
2. Otherwise runs your start command, **with the socket path appended as its
   final argument**, detached, with `PCONS_WORKER_IDLE_TIMEOUT` in its
   environment; then waits for the socket to appear.
3. Sends one request, and exits with the status that comes back.
4. **Runs the command directly** if any of that fails.

Step 4 is the one to keep in mind: a worker is an optimization. A build that
cannot reach one is slower, never broken, which is also what lets a generated
`build.ninja` work under plain ninja and in CI.

Two actions share a worker when their `Worker` compares equal — the same start
command, and the same `key`.

## Reading the generated command

A worker turns a short command in the build script into a long one in
`build.ninja`. It is worth being able to read it, because this is what you see
when a build using a worker misbehaves:

```
command = <python> .../workers/client.py <socket> 30 4 <python> .../workers/python_server.py --preload xml.dom.minidom -- <python> $source_0 $source_1 $out
          └─────── the client ──────────┘ └──1──┘ └2┘ └3┘ └──────── how to start a worker (4 tokens) ────────────┘ └4┘ └──────── the action ────────┘
```

1. **The socket** this worker listens on, named after the `Worker`'s identity.
2. **The idle timeout**, passed to the worker in the environment when the
   client starts one.
3. **How many tokens the start command occupies.** A count rather than a
   separator, so a start command containing `--` cannot be misread as the end
   of one.
4. **`--`**, after which everything is the action itself — exactly the command
   the build script asked for, and exactly what runs if no worker can be
   reached.

Two things follow that are useful when something is wrong:

- **To run the action by hand, take everything after the `--`.** That is the
  command with the worker removed, which is how you tell an action that is
  broken from a worker that is. Ask ninja for the version with `$in` and `$out`
  already filled in, and run it from the build directory:

  ```bash
  ninja -C build -t commands report.txt
  ```
- **To watch a worker start, run the start command yourself** with the socket
  path appended, and without redirecting its output. The client discards it,
  which is right for a build and unhelpful when you are debugging one.

The paths are absolute, including the interpreter's, for the same reason the
regeneration edge's are: a build directory should keep working from any
directory, and pin the tools it was configured with rather than whichever
happen to be on `PATH` later.

## What a worker must do

**Listen on the socket path it was given.** `AF_UNIX`, `SOCK_STREAM`. Bind a
temporary name and `rename()` it into place, so that two actions racing to
start a worker end with one live socket rather than a clobbered one. Create it
mode `0600`: a socket that runs commands should not be readable by other users.

**Accept a request.** One message, UTF-8 JSON, carrying three file descriptors
as `SCM_RIGHTS` ancillary data — the client's stdin, stdout and stderr, in that
order:

```json
{
  "argv": ["python", "render.py", "--out", "report.pdf"],
  "cwd": "/path/the/action/runs/in",
  "env": {"PATH": "...", "...": "..."},
  "stamp": "an opaque string identifying the client's environment"
}
```

**Run the action as the client would have.** In `cwd`, with `env` — not with
the environment the worker itself started in, which may be stale or simply
different. Write its output to the descriptors that came with the request:
they are ninja's pipes, so nothing needs relaying, and stdout and stderr keep
the order the program produced them in.

**Serve every action in isolation.** This is the contract's one hard
requirement. An action must not be able to observe anything a previous action
did — no cached value, no mutated global, no connection left mid-transaction.
`python_server.py` gets this by forking a child per request; a worker without
fork owes the same guarantee by its own means. Getting this wrong does not
produce a slow build, it produces a wrong one, silently.

**Reply with one JSON line**, and nothing else, on the connection:

```json
{"exit": 0}
```

`{"error": "..."}` instead, or a closed connection, means the action did not
run — the client will then run it directly. Refusing is always safe;
approximating is not. Put a human-readable reason in `error`: it is what
`PCONS_WORKER_DEBUG=1` shows, and a refusal is otherwise indistinguishable
from there being no worker at all.

`python_server.py` refuses anything that is not an interpreter running a
script, rather than reimplementing interpreter flags — and refuses a script
whose interpreter belongs to a different environment than its own, which
would otherwise run the action against a different set of packages than the
build script asked for.

**Refuse if forking would be unsafe.** If isolation comes from `fork`, check
before offering to serve — and check with the operating system.
`threading.active_count()` sees only Python threads, while the hazard is
native ones: OpenMP, TBB, a threaded BLAS, all of which numpy, scipy and
scikit-learn commonly start while being imported.

**Notice when it has gone stale.** `stamp` identifies the environment the
worker is being asked to serve — the client derives it from the *start
command's* interpreter, not from whatever is running pcons, which may be a
different installation entirely. A worker does not need to know how it is
made: **adopt the first stamp you are given, and stand down when a later
request carries a different one.** Installing, upgrading or removing a
package retires the worker holding the old copy, whatever language the worker
is written in.

The stamp watches `site-packages`, not `pyvenv.cfg`. That file is written once
when a virtualenv is created and never touched again — neither `uv pip
install` nor a `uv sync` that removes packages moves it — so a worker keyed on
it would go on serving last week's library, which is precisely what the stamp
exists to prevent.

**Exit when idle.** `PCONS_WORKER_IDLE_TIMEOUT` seconds without a request
means nobody is building any more. Nothing supervises workers, by design;
they are expected to go away on their own.

## When a worker is not being used

A refusal and an absent worker look identical from the outside: the build is
simply slower. Set `PCONS_WORKER_DEBUG=1` to be told which it was:

```
pcons worker: running directly (the action wants /a/.venv, this worker is /b/.venv)
```

It also stops the client discarding the worker's own stderr, which is the only
thing that explains a worker that will not start.

## What pcons guarantees in return

- The socket path is short enough for `AF_UNIX` (about 104 bytes), stable
  across builds for the same `Worker`, and inside a directory only the user
  can enter.
- The start command runs detached, with its output discarded. A worker is not
  a place to report to the terminal.
- Failing to reach a worker is never fatal, so a worker may be missing,
  broken, or slow to start without breaking a build.
