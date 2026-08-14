# SPDX-License-Identifier: MIT
"""Source location tracking for better error messages.

This module provides utilities to capture and format the source location
where pcons objects (nodes, targets, etc.) are defined, enabling
clear error messages that point to the exact line in the build script.
"""

from __future__ import annotations

import contextlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType


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
    """Capture the source location *depth* frames up the call stack
    (1 = immediate caller, 2 = caller's caller, ...)."""
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


#: This package's directory. Frames from inside it are pcons calling itself.
_PCONS_ROOT = str(Path(__file__).resolve().parent.parent)

#: Stdlib plumbing between a caller and pcons: `with env.override()` enters
#: through contextlib's __enter__, which must not be reported as the caller.
_SKIPPED_STDLIB_FILES = frozenset({str(Path(contextlib.__file__).resolve())})


def get_caller_location() -> SourceLocation:
    """Capture where a build script called into pcons.

    Frames inside pcons are skipped, so a convenience wrapper that forwards to
    another entry point — ``project.Command()`` delegating to
    ``env.Command()`` — reports the line somebody wrote rather than the line
    that forwarded it. A fixed depth cannot do both: it is right for one call
    path and wrong for the other, which is how a diagnostic ends up naming
    pcons's own source instead of the build script's.

    Falls back to the immediate caller when every frame is pcons's, which is
    pcons calling itself with nobody to blame.
    """
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None  # the caller of this function
        first = frame
        while frame is not None:
            filename = frame.f_code.co_filename
            resolved = str(Path(filename).resolve())
            if (
                not resolved.startswith(_PCONS_ROOT)
                and resolved not in _SKIPPED_STDLIB_FILES
            ):
                return _location_of(frame)
            frame = frame.f_back
        return _location_of(first) if first else SourceLocation("<unknown>", 0)
    finally:
        del frame


def _location_of(frame: FrameType) -> SourceLocation:
    """Describe one stack frame."""
    return SourceLocation(
        filename=frame.f_code.co_filename,
        lineno=frame.f_lineno,
        function=frame.f_code.co_name if frame.f_code.co_name != "<module>" else None,
    )
