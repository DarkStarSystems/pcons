# SPDX-License-Identifier: MIT
"""Write the release notes file, so the `release notes` verb has a target."""

import sys
from pathlib import Path

Path(sys.argv[1]).write_text("draft\n")
