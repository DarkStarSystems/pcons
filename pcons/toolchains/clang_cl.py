# SPDX-License-Identifier: MIT
"""Clang-CL toolchain implementation.

Clang-CL is LLVM's MSVC-compatible compiler driver for Windows.
It uses MSVC-style command-line flags and produces MSVC-compatible binaries,
making it a drop-in replacement for cl.exe while providing Clang's
diagnostics and optimizations.
"""

from __future__ import annotations

import functools
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder import CommandBuilder, MultiOutputBuilder, OutputSpec
from pcons.core.errors import PconsValueError
from pcons.core.preset import ToolContribution
from pcons.core.subst import NodeVar, SourcePath, TargetPath
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain, msvc_crt
from pcons.toolchains.msvc import MsvcAssembler, MsvcResourceCompiler
from pcons.tools.tool import BaseTool, Probe
from pcons.tools.toolchain import LinkNeeds

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.flags import FlagToken
    from pcons.core.target import Target


@functools.cache
def driver_link_args(clang_cl: str, flags: tuple[str, ...]) -> tuple[str, ...] | None:
    """The arguments of the link *clang_cl*'s driver would run with
    *flags*, from ``-###``, leaving out the linker, its output and the
    object. None if it can't say: it refuses the flags, as it does
    AddressSanitizer with a debug C runtime."""
    with tempfile.TemporaryDirectory() as tmp:
        obj = Path(tmp) / "probe.obj"
        obj.write_bytes(b"")
        try:
            result = subprocess.run(
                [clang_cl, *flags, "-###", str(obj)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    lines = result.stderr.strip().splitlines()
    if result.returncode != 0 or not lines:
        return None
    # The link is the last command, each argument quoted, with \\ and \"
    # escaped.
    args = [
        re.sub(r"\\(.)", r"\1", arg)
        for arg in re.findall(r'"((?:[^"\\]|\\.)*)"', lines[-1])
    ]
    return tuple(
        arg for arg in args[1:] if arg != str(obj) and not arg.startswith("-out:")
    )


def driver_link_additions(
    clang_cl: str, link: tuple[str, ...], flag: str
) -> tuple[str, ...] | None:
    """What *flag* adds to the link *clang_cl*'s driver would run with
    *link*, the C runtime (``/MD``) and ``/LD`` for a DLL: the runtime
    libraries a feature needs, and how they're linked. These change between
    LLVM releases, and with the C runtime and the kind of link, so the
    driver is asked. None if it can't say."""
    base = driver_link_args(clang_cl, link)
    with_flag = driver_link_args(clang_cl, (*link, flag))
    if base is None or with_flag is None:
        return None
    return tuple(arg for arg in with_flag if arg not in base)


def link_needs_from_args(args: tuple[str, ...]) -> LinkNeeds:
    """The link needs a driver's link arguments state (``-defaultlib:`` a
    library, ``-libpath:`` its directory, ``-nodefaultlib:`` a default
    library to leave out), so that whichever linker links writes them its
    own way. Any other argument is an error: it has no neutral form."""
    libs: list[str] = []
    libdirs: list[str] = []
    excluded: list[str] = []
    for arg in args:
        option, _, value = arg.partition(":")
        match option.lower().lstrip("/-"):
            case "defaultlib":
                libs.append(value.removesuffix(".lib"))
            case "libpath":
                libdirs.append(value)
            case "nodefaultlib" if value:
                excluded.append(value)
            case _:
                raise PconsValueError(
                    f"clang-cl's driver links with {arg!r}, which pcons can't "
                    f"state as a link need."
                )
    return LinkNeeds(
        libs=tuple(libs), libdirs=tuple(libdirs), excluded_default_libs=tuple(excluded)
    )


class ClangClCompiler(BaseTool):
    """Clang-CL C/C++ compiler tool.

    Uses MSVC-compatible flags like /O2, /W4, /I, /D, etc.
    """

    def __init__(self, name: str = "cc", language: str = "c") -> None:
        super().__init__(name, language=language)
        self._language = language

    def probe(self) -> Probe | None:
        base = super().probe()
        return base.msvc() if base else None

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang-cl",
            "flags": ["/nologo"],
            "iprefix": "/I",
            "includes": [],
            # clang-cl's own spelling for system includes; it also accepts
            # MSVC's /external:I, but -imsvc works on every version.
            "isysprefix": "-imsvc",
            "system_includes": [],
            "dprefix": "/D",
            "defines": [],
            # clang-cl uses /showIncludes for MSVC-style deps
            "objcmd": [
                "$cc.cmd" if self._language == "c" else "$cxx.cmd",
                "/showIncludes",
                "/c",
                TargetPath(prefix="/Fo"),
                "${prefix(cc.iprefix, cc.includes)}"
                if self._language == "c"
                else "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cc.isysprefix, cc.system_includes)}"
                if self._language == "c"
                else "${prefix(cxx.isysprefix, cxx.system_includes)}",
                "${prefix(cc.dprefix, cc.defines)}"
                if self._language == "c"
                else "${prefix(cxx.dprefix, cxx.defines)}",
                "$cc.flags" if self._language == "c" else "$cxx.flags",
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        suffixes = [".c"] if self._language == "c" else [".cpp", ".cxx", ".cc", ".C"]
        return {
            "Object": CommandBuilder(
                "Object",
                self.name,
                "objcmd",
                src_suffixes=suffixes,
                target_suffixes=[".obj"],
                language=self._language,
                single_source=True,
                deps_style="msvc",
            ),
        }


class ClangClCCompiler(ClangClCompiler):
    """Clang-CL C compiler."""

    env_var = "CC"

    def __init__(self) -> None:
        super().__init__("cc", "c")


class ClangClCxxCompiler(ClangClCompiler):
    """Clang-CL C++ compiler."""

    env_var = "CXX"

    def __init__(self) -> None:
        super().__init__("cxx", "cxx")


class ClangClLibrarian(BaseTool):
    """Librarian for clang-cl (uses llvm-lib or lib.exe)."""

    def __init__(self) -> None:
        super().__init__("lib")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "llvm-lib",
            "flags": ["/nologo"],
            "libcmd": [
                "$lib.cmd",
                "$lib.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary",
                "lib",
                "libcmd",
                src_suffixes=[".obj"],
                target_suffixes=[".lib"],
                single_source=False,
            ),
        }


class ClangClLinker(BaseTool):
    """Linker for clang-cl (uses lld-link or link.exe)."""

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "lld-link",
            "flags": ["/nologo"],
            "lprefix": "",
            "libs": [],
            "Lprefix": "/LIBPATH:",
            "libdirs": [],
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                TargetPath(prefix="/OUT:"),
                SourcePath(),
                NodeVar("LINK_ARCHIVES"),
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
            "sharedcmd": [
                "$link.cmd",
                "/DLL",
                "$link.flags",
                TargetPath(prefix="/OUT:", index=0),
                TargetPath(prefix="/IMPLIB:", index=1),
                SourcePath(),
                NodeVar("LINK_ARCHIVES"),
                "${prefix(link.Lprefix, link.libdirs)}",
                "$link.libs",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[".obj", ".lib", ".res"],
                target_suffixes=[".exe"],
                single_source=False,
            ),
            "SharedLibrary": MultiOutputBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                outputs=[
                    OutputSpec("primary", ".dll"),
                    OutputSpec("import_lib", ".lib"),
                ],
                src_suffixes=[".obj", ".lib"],
                single_source=False,
            ),
        }


class ClangClToolchain(MsvcCompatibleToolchain):
    """Clang-CL toolchain: MSVC-compatible flags and binaries on Windows."""

    ENV_COMPILER_FAMILY = "clang-cl"

    # MSVC's /GL emits its proprietary IL, which clang-cl warns about and
    # ignores — so the shared realization would silently build without LTO.
    # clang-cl spells it the clang way: -flto bitcode objects, which
    # lld-link recognizes and link-time-optimizes on its own; /LTCG is
    # accepted there but unneeded.
    # clang treats /Ob3 as /Ob2, and a clang-cl before 19 (LLVM 19, September
    # 2024; still the LLVM of Ubuntu 24.04 and of Visual Studio 17.10 and
    # 17.11) warns about it as an unknown argument, so release-fastest is
    # /O2 alone here.
    MSVC_VARIANTS: dict[str, tuple[list[str], list[str]]] = {
        **MsvcCompatibleToolchain.MSVC_VARIANTS,
        "release-fastest": (["/O2"], ["NDEBUG"]),
    }

    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        **MsvcCompatibleToolchain.FEATURE_PRESETS,
        "lto": {
            "compile_flags": ["-flto"],
        },
    }

    TOOL_NAMES = ("cc", "cxx", "lib", "link", "rc", "ml")

    #: Driver flags that hand the token after them to a sub-tool. Consecutive
    #: ones form a single directive, so they group as a run and are never
    #: split by de-duplication (see UnixToolchain.PASSTHROUGH_FLAGS).
    PASSTHROUGH_FLAGS: frozenset[str] = frozenset(["-Xlinker", "-Xclang"])

    # Clang-CL supports additional GCC-style flags beyond the base MSVC set
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # MSVC-style linker passthrough
            "/link",
            # GCC-style flags that clang-cl also supports
            "-target",
            "--target",
            *PASSTHROUGH_FLAGS,
        ]
    )

    def get_passthrough_flags(self) -> frozenset[str]:
        """Flags whose argument is passed through to a sub-tool verbatim."""
        return self.PASSTHROUGH_FLAGS

    def __init__(self) -> None:
        super().__init__("clang-cl")

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """clang-cl's objects don't name their AddressSanitizer or OpenMP
        runtime, as cl's do: its own driver adds them when it links, and
        pcons links with lld-link or link.exe. The AddressSanitizer runtime
        is the linker's to choose (see _sanitizer_link_args); libomp is
        what the driver adds for /openmp."""
        needs = super().link_needs(target, languages, link_language, env)
        if not languages & {"c", "cxx"}:
            return needs
        if env.feature_realized("sanitize", self):
            needs = needs | LinkNeeds(sanitizers=("address",))
        if env.feature_realized("openmp", self):
            args = driver_link_additions(self._driver(env), ("/MD",), "/openmp")
            if not args:
                raise PconsValueError(
                    f"Target '{target.name}': clang-cl doesn't say how to link "
                    f"its OpenMP runtime (libomp, beside clang-cl)."
                )
            needs = needs | link_needs_from_args(args)
        return needs

    def _sanitizer_link_args(
        self, sanitizers: Sequence[str], target: Target
    ) -> list[FlagToken]:
        """clang's AddressSanitizer runtime, as clang-cl's driver would link
        it for this link's C runtime: a different library for /MD and /MT,
        and with /MT, a DLL links only a thunk to the program's. Any
        sanitized object in the program uses it, ifx's too."""
        if "address" not in sanitizers:
            return []
        env = target._env
        if env is None:
            # A pcons bug, not a user error.
            raise ValueError(f"Target '{target.name}' has no environment to link.")
        crt = self.MSVC_CRT_FLAGS[msvc_crt(env.runtime_linkage, env.variant)]
        link = (crt, "/LD") if target.target_type == "shared_library" else (crt,)
        args = driver_link_additions(self._driver(env), link, "-fsanitize=address")
        if args is None:
            hint = (
                " clang's AddressSanitizer has no debug C runtime: build with "
                "relwithdebinfo rather than debug."
                if crt.endswith("d")
                else ""
            )
            raise PconsValueError(
                f"Target '{target.name}': clang-cl can't link AddressSanitizer "
                f"with the {crt} C runtime.{hint}"
            )
        return list(args)

    @staticmethod
    def _driver(env: Environment) -> str:
        """The clang-cl *env* compiles with."""
        return str((env.cxx if env.has_tool("cxx") else env.cc).cmd)

    # Architecture to Clang target triple mapping for Windows
    CLANG_CL_TARGET_MAP: dict[str, str] = {
        "x64": "x86_64-pc-windows-msvc",
        "x86": "i686-pc-windows-msvc",
        "arm64": "aarch64-pc-windows-msvc",
        # Common aliases
        "amd64": "x86_64-pc-windows-msvc",
        "x86_64": "x86_64-pc-windows-msvc",
        "i386": "i686-pc-windows-msvc",
        "i686": "i686-pc-windows-msvc",
        "aarch64": "aarch64-pc-windows-msvc",
    }

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE:xxx (via base) plus --target for cross-compilation.

        clang-cl retargets with a flag (one binary), but a cross arch still
        needs the target's VC and Windows SDK libraries — the dev shell's LIB
        covers only the host arch — so those are added as /LIBPATH: flags.
        """
        from pcons.toolchains.msvc import cross_arch_contributions

        contribs = super()._arch_contributions(arch)
        target_triple = self.CLANG_CL_TARGET_MAP.get(arch.lower())
        if target_triple:
            flag = f"--target={target_triple}"
            contribs.append(ToolContribution("cc", flags=(flag,)))
            contribs.append(ToolContribution("cxx", flags=(flag,)))

        contribs.extend(cross_arch_contributions(arch, repoint_tools=False))
        return contribs


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    ClangClToolchain,
    aliases=["clang-cl"],
    check_command="clang-cl",
    tool_classes=[
        ClangClCCompiler,
        ClangClCxxCompiler,
        ClangClLibrarian,
        ClangClLinker,
        MsvcResourceCompiler,
        MsvcAssembler,
    ],
    category="c",
    platforms=["win32"],
    description="Clang with MSVC-compatible flags",
    finder="find_c_toolchain()",
)
