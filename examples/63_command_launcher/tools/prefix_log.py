# SPDX-License-Identifier: MIT
"""A stand-in for ccache or time: note that we ran, then run the real command.

    prefix_log.py <label> <logfile> <command> [args...]

Everything after the logfile is the command this wraps -- which, when
launchers are stacked, is the next launcher along. Real ones (ccache,
sccache, time, valgrind) work the same way, and a build machine is not
guaranteed to have any of them, so the example ships its own.
"""

import subprocess
import sys
import time
from pathlib import Path

label, logfile, *command = sys.argv[1:]

start = time.perf_counter()
result = subprocess.run(command)
elapsed = time.perf_counter() - start

# Append rather than truncate: several compiles share one log, and parallel
# builds may hold it open at once.
with Path(logfile).open("a", encoding="utf-8") as log:
    log.write(f"{label} {elapsed:.3f}s {Path(command[-1]).name}\n")

sys.exit(result.returncode)
