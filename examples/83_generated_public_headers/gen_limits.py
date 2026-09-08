# SPDX-License-Identifier: MIT
"""Write the library's public header, slowly and atomically.

The sleep is what makes this example a regression test: with no sleep a
generator that is not ordered first still finishes first often enough that
the build looks correct. The rename is how real code generators write, and
it moves the directory's timestamp on every run, which is what a build-time
scanner of that directory notices.
"""

import os
import sys
import time
from pathlib import Path

time.sleep(1.0)

header = Path(sys.argv[1])
header.parent.mkdir(parents=True, exist_ok=True)
tmp = header.with_suffix(".tmp")
tmp.write_text(
    '#pragma once\n#define METRICS_BUCKETS 8\n#define METRICS_LABEL "generated"\n'
)
os.replace(tmp, header)
