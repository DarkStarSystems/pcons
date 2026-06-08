# SPDX-License-Identifier: MIT
"""Shared GNU-style command-line conventions for GCC, Clang, and gfortran.

GCC, LLVM/Clang, and gfortran share the same option syntax: -I/-D for
includes/defines, -l/-L for libraries, -F/-framework for macOS frameworks, and
-MD/-MF for dependency generation. These factories build the `default_vars`
dicts so each tool only specifies what differs (its command name and any extra
keys).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath

if TYPE_CHECKING:
    from pcons.core.builder import Builder


def gnu_compile_vars(cmd: str, ns: str) -> dict[str, object]:
    """Default vars for a GNU-style compile tool (gcc/g++/clang/clang++).

    Args:
        cmd: Default compiler command (e.g. "gcc", "clang++").
        ns: Tool namespace ("cc" or "cxx"), used to build the $ns.* references.
    """
    return {
        "cmd": cmd,
        "flags": [],
        "iprefix": "-I",
        "includes": [],
        "dprefix": "-D",
        "defines": [],
        "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
        "objcmd": [
            f"${ns}.cmd",
            f"${ns}.flags",
            f"${{prefix({ns}.iprefix, {ns}.includes)}}",
            f"${{prefix({ns}.dprefix, {ns}.defines)}}",
            f"${ns}.depflags",
            "-c",
            "-o",
            TargetPath(),
            SourcePath(),
        ],
    }


def _link_tail() -> list[object]:
    """Tokens shared by progcmd and sharedcmd (fresh instances per call)."""
    return [
        "-o",
        TargetPath(),
        SourcePath(),
        "${prefix(link.Lprefix, link.libdirs)}",
        "${prefix(link.lprefix, link.libs)}",
        "${prefix(link.Fprefix, link.frameworkdirs)}",
        "${pairwise(link.fprefix, link.frameworks)}",
    ]


def gnu_link_vars(cmd: str) -> dict[str, object]:
    """Default vars for a GNU-style link tool (gcc/clang/gfortran).

    Args:
        cmd: Default linker command (e.g. "gcc", "clang", "gfortran").
    """
    shared_flag = "-dynamiclib" if get_platform().is_macos else "-shared"
    return {
        "cmd": cmd,
        "flags": [],
        "lprefix": "-l",
        "libs": [],
        "Lprefix": "-L",
        "libdirs": [],
        # Framework support (macOS only, but always defined for portability)
        "Fprefix": "-F",
        "frameworkdirs": [],
        "fprefix": "-framework",
        "frameworks": [],
        "progcmd": ["$link.cmd", "$link.flags", *_link_tail()],
        "sharedcmd": ["$link.cmd", shared_flag, "$link.flags", *_link_tail()],
    }


def gnu_link_builders() -> dict[str, Builder]:
    """Program and SharedLibrary builders shared by GNU-style linkers.

    Identical for gcc, clang, and gfortran: link object files into an
    executable (progcmd) or a shared library (sharedcmd).
    """
    platform = get_platform()
    return {
        "Program": CommandBuilder(
            "Program",
            "link",
            "progcmd",
            src_suffixes=[platform.object_suffix],
            target_suffixes=[platform.exe_suffix],
            single_source=False,
        ),
        "SharedLibrary": MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", platform.shared_lib_suffix),
            ],
            src_suffixes=[platform.object_suffix],
            single_source=False,
        ),
    }
