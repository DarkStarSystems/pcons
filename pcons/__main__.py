# SPDX-License-Identifier: MIT
"""Entry point for `python -m pcons`."""

import sys

from pcons.cli import main

if __name__ == "__main__":
    sys.exit(main())
