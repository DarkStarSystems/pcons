# SPDX-License-Identifier: MIT
"""Word counting, imported by the build script's report function."""


def count(text: str) -> int:
    return len(text.split())
