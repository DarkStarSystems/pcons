# SPDX-License-Identifier: MIT
"""A stand-in for ccache or time: note that we ran, then run the real command.

    prefix_log.py <label> <logdir> <command> [args...]

Everything after the log directory is the command this wraps -- which, when
launchers are stacked, is the next launcher along. Real ones (ccache,
sccache, time, valgrind) work the same way, and a build machine is not
guaranteed to have any of them, so the example ships its own.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

label, logdir, *command = sys.argv[1:]

start = time.perf_counter()
result = subprocess.run(command)
elapsed = time.perf_counter() - start

# Every launcher process writes its own file: several of them run at once,
# and no two processes may share one file. The finish time leads, so the
# records can be put back in order.
record = f"{time.time():.6f} {label} {elapsed:.3f}s {Path(command[-1]).name}\n"
Path(logdir).mkdir(parents=True, exist_ok=True)
Path(logdir, f"{label}-{os.getpid()}.log").write_text(record, encoding="utf-8")

sys.exit(result.returncode)
