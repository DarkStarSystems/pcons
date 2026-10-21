# SPDX-License-Identifier: MIT
"""Shared GNU-style command-line conventions for GCC, Clang, and gfortran.

GCC, LLVM/Clang, and gfortran share the same option syntax: -I/-D for
includes/defines, -l/-L for libraries, -F/-framework for macOS frameworks, and
-MD/-MF for dependency generation. These factories build the `default_vars`
dicts and `builders()` maps so each tool only specifies what differs (its
command name and any extra keys).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pcons.configure.platform import Platform, get_platform
from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.errors import PconsValueError
from pcons.core.subst import NodeVar, SourcePath, TargetPath

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.builder import Builder


# Flags whose argument is a directory path, in either form ("-Ifoo" or
# "-I foo"), on a GNU-style compiler's and linker's command lines.
# Generators rewrite these so generated build files stay relocatable.
# Deliberately excludes -include/-imacros: their argument is a header *name*
# resolved through the include path (Qt's mkspecs pass "-include
# arm_acle.h"), and rewriting it as a path breaks the build.
GNU_COMPILE_PATH_FLAGS: frozenset[str] = frozenset(
    [
        "-I",
        "-F",
        "-isystem",
        "-iquote",
        "-idirafter",
        "-iframework",
        "-isysroot",
        "--sysroot",
    ]
)
GNU_LINK_PATH_FLAGS: frozenset[str] = frozenset(["-L", "-F", "-isysroot", "--sysroot"])


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
            f"${{prefix({ns}.isysprefix, {ns}.system_includes)}}",
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
        # Third-party headers: found like any other include, but warnings
        # from them are suppressed.
        "isysprefix": "-isystem",
        "system_includes": [],
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
        # Archives that link each other in a cycle, grouped; see
        # UnixToolchain.link_group_tokens. Empty on every other edge.
        NodeVar("LINK_GROUPS"),
        "${prefix(link.Lprefix, link.libdirs)}",
        "${prefix(link.lprefix, link.libs)}",
        "${prefix(link.Fprefix, link.frameworkdirs)}",
        "${prefix(link.fprefix, link.frameworks)}",
    ]


#: Systems whose shared libraries need position-independent code.
_PIC_SYSTEMS = frozenset({"linux", "android", "freebsd", "openbsd", "netbsd"})


def needs_pic(target_type: str, platform: Platform, pic: bool | None = None) -> bool:
    """Whether *target_type* compiles as position-independent code for
    *platform*. On Linux, Android and the BSDs a shared library must, and a
    static or object library does too unless *pic* is False, so it can go
    into one, as Meson builds them. 64-bit macOS code is PIC already,
    Windows DLLs don't use it, and bare-metal targets have no loader."""
    if platform.os not in _PIC_SYSTEMS:
        return False
    if target_type == "shared_library":
        return True
    return target_type in ("static_library", "object") and pic is not False


def require_cross_binaries(
    preset: Any, compiler: str, tools: Sequence[str], example: str, instead: str
) -> None:
    """Raise if *preset* targets a triple but repoints none of *tools*.

    GCC-family compilers (*compiler*) reject --target= and select targets
    by binary, so without cross-compiler commands the build would silently
    target the host. *example* is a tool_cmds entry, with ``{triple}`` in
    it; *instead* the toolchain that retargets by flag. A wasm triple is
    let through, for UnixToolchain's "use the dedicated toolchain" error.
    """
    triple = str(getattr(preset, "triple", None) or "")
    if not triple or triple.startswith("wasm32"):
        return
    resolve = getattr(preset, "resolved_tool_cmds", None)
    cmds = resolve() if resolve is not None else {}
    if any(tool in cmds for tool in tools):
        return
    name = getattr(preset, "name", preset)
    raise PconsValueError(
        f"Cross preset '{name}' targets triple '{triple}', but {compiler} "
        f"selects targets by binary, not by flag. Provide cross-compiler "
        f"commands in the preset's tool_cmds (e.g. "
        f"{example.format(triple=triple)}), or use {instead}, which "
        f"retargets via --target."
    )


def gnu_link_vars(cmd: str) -> dict[str, object]:
    """Default vars for a GNU-style link tool (gcc/clang/gfortran).

    Args:
        cmd: Default linker command (e.g. "gcc", "clang", "gfortran").
    """
    return {
        "cmd": cmd,
        "flags": [],
        # Every GNU-style driver takes -shared, for Mach-O too (flang
        # takes nothing else).
        "sharedflag": "-shared",
        "lprefix": "-l",
        "libs": [],
        "Lprefix": "-L",
        "libdirs": [],
        # Framework support (macOS only, but always defined for portability).
        # Passed to the linker with -Wl, which every GNU-style driver
        # takes: flang's takes neither -framework nor -F itself.
        "Fprefix": "-Wl,-F",
        "frameworkdirs": [],
        "fprefix": "-Wl,-framework,",
        "frameworks": [],
        "progcmd": ["$link.cmd", "$link.flags", *_link_tail()],
        "sharedcmd": ["$link.cmd", "$link.sharedflag", "$link.flags", *_link_tail()],
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
