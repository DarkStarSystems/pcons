# SPDX-License-Identifier: MIT
"""Source location tracking for better error messages.

This module provides utilities to capture and format the source location
where pcons objects (nodes, targets, etc.) are defined, enabling
clear error messages that point to the exact line in the build script.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A location in a source file.

    Attributes:
        filename: The path to the source file.
        lineno: The line number (1-indexed).
        function: The name of the function containing this location.
    """

    filename: str
    lineno: int
    function: str | None = None

    def __str__(self) -> str:
        """Format as 'filename:lineno' or 'filename:lineno in function()'."""
        base = f"{self.filename}:{self.lineno}"
        if self.function:
            return f"{base} in {self.function}()"
        return base

    @property
    def short_filename(self) -> str:
        """Return just the filename without the full path."""
        return Path(self.filename).name


def get_source_location(depth: int = 1) -> SourceLocation:
    """Capture the source location of the caller.

    Args:
        depth: How many frames to skip. 1 = immediate caller,
               2 = caller's caller, etc.

    Returns:
        SourceLocation for the specified caller.
    """
    # Add 1 to skip this function itself
    frame = inspect.currentframe()
    try:
        for _ in range(depth + 1):
            if frame is None:
                break
            frame = frame.f_back

        if frame is None:
            return SourceLocation("<unknown>", 0)

        return SourceLocation(
            filename=frame.f_code.co_filename,
            lineno=frame.f_lineno,
            function=frame.f_code.co_name
            if frame.f_code.co_name != "<module>"
            else None,
        )
    finally:
        del frame  # Avoid reference cycles


def get_caller_location() -> SourceLocation:
    """Capture the source location of the caller's caller.

    This is a convenience function for the common case where a method
    wants to record where it was called from (skipping itself).

    Returns:
        SourceLocation for the caller's caller.
    """
    return get_source_location(depth=2)
