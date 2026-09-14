# SPDX-License-Identifier: MIT
"""What an Android SDK holds, for the build scripts that package with it.

Compiling needs the NDK, which ``android()`` is given directly. Packaging
needs the SDK, whose tools live in a directory named after a revision, one
per revision installed:

    from pcons.toolchains.android import build_tools_program, newest_build_tools

    zipalign = build_tools_program(sdk, "zipalign")
    revision = newest_build_tools(sdk).name

Picking the newest is what every Android build does, and picking it by
name is the trap: "9.0.0" sorts above "37.0.0" as a string and is four
years older.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform

if TYPE_CHECKING:
    from collections.abc import Iterator


def _revision(name: str) -> tuple[int, ...]:
    """A build-tools directory name, ordered as the version it is."""
    return tuple(int(part) if part.isdigit() else 0 for part in name.split("."))


def _revisions(sdk: str | Path) -> Iterator[Path]:
    """Every build-tools revision directory under *sdk*, in no order."""
    return (p for p in (Path(sdk) / "build-tools").glob("*") if p.is_dir())


def _program(revision: Path, program: str) -> Path | None:
    """*program* inside one revision directory, spelled for this host.

    The build tools are a mixture: on Windows ``apksigner`` is a ``.bat``
    wrapper and ``zipalign`` an ``.exe``, and neither answers to the other's
    extension.
    """
    suffixes = (".bat", ".exe") if get_platform().is_windows else ("",)
    for suffix in suffixes:
        candidate = revision / f"{program}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def newest_build_tools(sdk: str | Path) -> Path:
    """The highest build-tools revision installed under *sdk*.

    Args:
        sdk: The Android SDK root.

    Returns:
        The revision directory. Its ``name`` is the revision itself, which
        is what androiddeployqt wants in ``sdkBuildToolsRevision``.

    Raises:
        ValueError: If no revision is installed.
    """
    installed = sorted(_revisions(sdk), key=lambda p: _revision(p.name))
    if not installed:
        raise ValueError(
            f"No build-tools revision under {Path(sdk) / 'build-tools'}. "
            f"Install one with the SDK manager."
        )
    return installed[-1]


def build_tools_program(sdk: str | Path, program: str) -> Path:
    """*program* from the highest build-tools revision that installs it.

    Not simply the highest revision: a partial install, or one predating the
    program, has the rest of the tools and not this one.

    Args:
        sdk: The Android SDK root.
        program: The program name, without any host extension.

    Returns:
        The path to run.

    Raises:
        ValueError: If no installed revision holds *program*.
    """
    found = [
        path
        for revision in _revisions(sdk)
        if (path := _program(revision, program)) is not None
    ]
    if not found:
        raise ValueError(
            f"No {program} under {Path(sdk) / 'build-tools'}. Install the "
            f"SDK build tools."
        )
    return max(found, key=lambda p: _revision(p.parent.name))
