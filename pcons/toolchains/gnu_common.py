# SPDX-License-Identifier: MIT
"""Shared GNU-style command-line conventions for GCC, Clang, and gfortran.

GCC, LLVM/Clang, and gfortran share the same option syntax: -I/-D for
includes/defines, -l/-L for libraries, -F/-framework for macOS frameworks, and
-MD/-MF for dependency generation. These factories build the `default_vars`
dicts and `builders()` maps so each tool only specifies what differs (its
command name and any extra keys).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath

if TYPE_CHECKING:
    from pcons.core.builder import Builder


def gnu_compile_vars(
    cmd: str,
    ns: str,
    *,
    target_tokens: list[str] | None = None,
    extra_vars: dict[str, object] | None = None,
) -> dict[str, object]:
    """Default vars for a GNU-style compile tool (gcc/g++/clang/clang++).

    Args:
        cmd: Default compiler command (e.g. "gcc", "clang++").
        ns: Tool namespace ("cc" or "cxx"), used to build the $ns.* references.
        target_tokens: Extra tokens inserted right after the command, before
            flags (e.g. ["--target=wasm32-wasi", "$cc.sysroot_flag"] for WASI).
        extra_vars: Additional default vars merged into the result (e.g. WASI's
            "sysroot_flag" placeholder).
    """
    objcmd: list[object] = [f"${ns}.cmd"]
    if target_tokens:
        objcmd.extend(target_tokens)
    objcmd.extend(
        [
            f"${ns}.flags",
            f"${{prefix({ns}.iprefix, {ns}.includes)}}",
            f"${{prefix({ns}.dprefix, {ns}.defines)}}",
            f"${ns}.depflags",
            "-c",
            "-o",
            TargetPath(),
            SourcePath(),
        ]
    )
    result: dict[str, object] = {
        "cmd": cmd,
        "flags": [],
        "iprefix": "-I",
        "includes": [],
        "dprefix": "-D",
        "defines": [],
        "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
        "objcmd": objcmd,
    }
    if extra_vars:
        result.update(extra_vars)
    return result


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


# src_suffixes and language for each compile namespace.
_COMPILE_SOURCES: dict[str, tuple[list[str], str]] = {
    "cc": ([".c"], "c"),
    "cxx": ([".cpp", ".cxx", ".cc", ".C"], "cxx"),
}


def gnu_compile_builders(
    ns: str, *, object_suffix: str | None = None
) -> dict[str, Builder]:
    """The {'Object': ...} builder for a GNU-style compile tool.

    Args:
        ns: Tool namespace ("cc" or "cxx").
        object_suffix: Object-file suffix. Defaults to the host platform's
            (".o"/".obj"); pass ".o" explicitly for wasm toolchains that always
            emit ".o" regardless of host.
    """
    src_suffixes, language = _COMPILE_SOURCES[ns]
    if object_suffix is None:
        object_suffix = get_platform().object_suffix
    return {
        "Object": CommandBuilder(
            "Object",
            ns,
            "objcmd",
            src_suffixes=src_suffixes,
            target_suffixes=[object_suffix],
            language=language,
            single_source=True,
            depfile=TargetPath(suffix=".d"),
            deps_style="gcc",
        ),
    }


def gnu_archiver_vars(cmd: str) -> dict[str, object]:
    """Default vars for a GNU-style archiver (ar, llvm-ar, emar).

    Args:
        cmd: Default archiver command.
    """
    return {
        "cmd": cmd,
        "flags": ["rcs"],
        "libcmd": ["$ar.cmd", "$ar.flags", TargetPath(), SourcePath()],
    }


def gnu_archiver_builders(
    *, object_suffix: str | None = None, static_lib_suffix: str | None = None
) -> dict[str, Builder]:
    """The {'StaticLibrary': ...} builder for a GNU-style archiver.

    Suffixes default to the host platform's; pass ".o"/".a" explicitly for wasm
    toolchains that use those regardless of host.
    """
    platform = get_platform()
    if object_suffix is None:
        object_suffix = platform.object_suffix
    if static_lib_suffix is None:
        static_lib_suffix = platform.static_lib_suffix
    return {
        "StaticLibrary": CommandBuilder(
            "StaticLibrary",
            "ar",
            "libcmd",
            src_suffixes=[object_suffix],
            target_suffixes=[static_lib_suffix],
            single_source=False,
        ),
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
