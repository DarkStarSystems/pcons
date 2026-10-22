# SPDX-License-Identifier: MIT
"""Shared base class for MSVC-compatible toolchains (MSVC and clang-cl)."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from pcons.core.errors import PconsValueError
from pcons.core.preset import Preset, ToolContribution
from pcons.tools.toolchain import BaseToolchain, LinkNeeds

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pcons.configure.platform import Platform
    from pcons.core.environment import Environment
    from pcons.core.flags import FlagToken
    from pcons.core.target import Target
    from pcons.toolchains.build_context import CompileLinkContext
    from pcons.tools.toolchain import AuxiliaryInputHandler, SourceHandler


def msvc_crt(linkage: str, variant: str) -> str:
    """The C runtime every compiler in an MSVC-ABI program links, as
    clang's ``-fms-runtime-lib`` names it: the DLL one ("dll"), or with
    static runtime linkage the static one ("static"), in its debug build
    ("dll_dbg", "static_dbg") under the debug variant.

    The DLL one is the default because it's what CMake and Conan
    (``compiler.runtime=dynamic``) build with, so packages match; the
    compilers' own default is the static one. Every compiler realizes this
    one choice, since objects built for different runtimes don't link.
    """
    crt = "static" if linkage == "static" else "dll"
    return f"{crt}_dbg" if variant.lower() == "debug" else crt


class MsvcCompatibleToolchain(BaseToolchain):
    """Base class for MSVC-compatible toolchains (MSVC and clang-cl).

    Shares source/auxiliary-input handling, output naming, /MACHINE:
    arch flags, and build variants; subclasses override where the two
    toolchains differ.
    """

    # Named feature presets; see docs/presets.md.
    FEATURE_PRESETS: dict[str, dict[str, list[str]]] = {
        "warnings": {
            "compile_flags": ["/W4"],
        },
        "werror": {
            "compile_flags": ["/WX"],
        },
        "sanitize": {
            "compile_flags": ["/fsanitize=address"],
        },
        "profile": {
            "link_flags": ["/PROFILE"],
        },
        "lto": {
            "compile_flags": ["/GL"],
            "link_flags": ["/LTCG"],
        },
        "hardened": {
            "compile_flags": ["/GS", "/guard:cf"],
            "link_flags": ["/DYNAMICBASE", "/NXCOMPAT", "/guard:cf"],
        },
        # MSVC's objects carry the OpenMP runtime as a /DEFAULTLIB directive
        # (vcomp), so no link flags; clang-cl's link needs add its libomp.
        "openmp": {
            "compile_flags": ["/openmp"],
        },
        "fast-math": {
            "compile_flags": ["/fp:fast"],
        },
        # No "pthread" or "coverage" here: guard portable scripts with
        # env.has_preset(...).
    }

    # Architecture to MSVC machine type mapping (shared by MSVC and clang-cl)
    MSVC_MACHINE_MAP: dict[str, str] = {
        "x64": "X64",
        "x86": "X86",
        "arm64": "ARM64",
        "arm64ec": "ARM64EC",
        # Common aliases
        "amd64": "X64",
        "x86_64": "X64",
        "i386": "X86",
        "i686": "X86",
        "aarch64": "ARM64",
    }

    # Flags that take their argument as a separate token.
    # Subclasses can extend this with toolchain-specific flags.
    SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
        [
            # Linker passthrough (when invoking cl.exe which calls link.exe)
            "/link",
        ]
    )

    @property
    def abi(self) -> str | None:
        """MSVC's, which clang-cl, flang and ifx share on Windows."""
        return "msvc"

    def compile_link_context_class(self) -> type[CompileLinkContext]:
        """MSVC and clang-cl both use MSVC-style command formatting.

        This selects MsvcCompileLinkContext, which formats library names as
        ``foo.lib`` (rather than the bare ``foo`` of the GNU-style base).
        Without it, clang-cl's linker fails with "could not open 'foo'" on
        any library referenced by name (e.g. from an imported package).
        """
        from pcons.toolchains.build_context import MsvcCompileLinkContext

        return MsvcCompileLinkContext

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Return handler for C, C++, resource (.rc), or assembly (.asm) files."""
        from pcons.tools.toolchain import SourceHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".obj", None, "msvc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        # Handle .C as C++ (common convention, though case-insensitive on Windows)
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".obj", None, "msvc")
        if suffix_lower == ".rc":
            # Resource files compile to .res and have no depfile
            return SourceHandler("rc", "resource", ".res", None, None, "rccmd")
        if suffix_lower == ".asm":
            # MASM assembly files - compiled with ml64.exe (x64) or ml.exe (x86)
            return SourceHandler("ml", "asm", ".obj", None, None, "asmcmd")
        return None

    def get_import_library_name(
        self, shared_library_name: str, target: Platform | None = None
    ) -> str | None:
        """``foo.dll`` is linked against through ``foo.lib``.

        The link step writes it (the ``/IMPLIB:`` in ``sharedcmd``), so the
        name follows the DLL's, keeping any directory and any name the target
        chose with output_prefix/output_name/output_filename. These toolchains
        only ever build for Windows, so *target* does not change the answer.
        """
        return str(PurePosixPath(shared_library_name).with_suffix(".lib"))

    def realize_link_needs(
        self,
        needs: LinkNeeds,
        target: Target,
        existing_flags: Sequence[FlagToken],
        driver: str | None = None,
    ) -> list[FlagToken]:
        """Objects as they are, and ``/SUBSYSTEM:CONSOLE`` for a console
        entry point the objects don't define (ifx's ``main`` is in its
        runtime, so link.exe can't infer one), unless the project chose a
        subsystem: a C++ ``WinMain`` program sets ``/SUBSYSTEM:WINDOWS``.
        Windows has no rpaths or frameworks. Default libraries the objects
        mustn't bring are ``/NODEFAULTLIB:``."""
        if needs.rpaths or needs.frameworks:
            raise PconsValueError(
                f"Target '{target.name}': {self.name} links for Windows, which "
                f"has no rpaths or frameworks, but its objects need {needs}."
            )
        flags: list[FlagToken] = [
            *needs.objects,
            *self._sanitizer_link_args(needs.sanitizers, target),
        ]
        if needs.console and not any(
            isinstance(f, str) and f.upper().startswith("/SUBSYSTEM:")
            for f in existing_flags
        ):
            flags.append("/SUBSYSTEM:CONSOLE")
        flags += [f"/NODEFAULTLIB:{lib}" for lib in needs.excluded_default_libs]
        return flags

    def _sanitizer_link_args(
        self, sanitizers: Sequence[str], target: Target
    ) -> list[FlagToken]:
        """link.exe's own AddressSanitizer runtime, for objects that don't
        name it (ifx's). cl's name it themselves."""
        return ["/INFERASANLIBS"] if "address" in sanitizers else []

    def get_link_flags_for_target(
        self,
        target: Target,
        output_name: str,
        existing_flags: Sequence[FlagToken],
    ) -> list[FlagToken]:
        """A module-definition file for ``exported_symbols``."""
        symbols = (
            target.get_option("exported_symbols")
            if hasattr(target, "get_option")
            else None
        )
        if not symbols or target.target_type not in ("shared_library", "program"):
            return []
        text = module_definition(target, symbols)
        return [self._link_input_token(target, ".def", text, "/DEF:")]

    def get_auxiliary_input_handler(self, suffix: str) -> AuxiliaryInputHandler | None:
        """Return handler for .def (module definition) and .manifest files."""
        from pcons.tools.toolchain import AuxiliaryInputHandler

        suffix_lower = suffix.lower()
        if suffix_lower == ".def":
            return AuxiliaryInputHandler(".def", "/DEF:$file")
        if suffix_lower == ".manifest":
            # Both MSVC and lld-link require /MANIFEST:EMBED with /MANIFESTINPUT
            return AuxiliaryInputHandler(
                ".manifest",
                "/MANIFESTINPUT:$file",
                extra_flags=["/MANIFEST:EMBED"],
            )
        return None

    def get_object_suffix(self) -> str:
        """Return the object file suffix (.obj for MSVC-compatible)."""
        return ".obj"

    def get_archiver_tool_name(self) -> str:
        """Return the archiver tool name (lib for MSVC-compatible)."""
        return "lib"

    def get_compile_flags_for_target_type(
        self,
        target_type: str,
        env: Environment | None = None,
        *,
        pic: bool | None = None,
    ) -> list[str]:
        """Return additional compile flags for target type.

        MSVC-compatible toolchains don't need special flags like -fPIC.
        DLL exports are handled via __declspec or .def files.
        """
        return []

    # Flags whose argument is a path. MSVC writes them joined ("/Ifoo"), but
    # the separate form is accepted too, so generators handle both.
    COMPILE_PATH_FLAGS: ClassVar[frozenset[str]] = frozenset(
        [
            "/I",
            "/external:I",
            "-imsvc",  # clang-cl's form of /external:I
        ]
    )
    # Auxiliary linker inputs (get_auxiliary_input_handler) are node paths,
    # which for a source-tree .manifest or .def need the same rewrite.
    LINK_PATH_FLAGS: ClassVar[frozenset[str]] = frozenset(
        ["/LIBPATH:", "/MANIFESTINPUT:", "/DEF:"]
    )

    def get_separated_arg_flags(self) -> frozenset[str]:
        """Return flags that take their argument as a separate token."""
        return self.SEPARATED_ARG_FLAGS

    # Variant flags per build type (compile_flags, defines). The C runtime
    # is the runtime linkage's (msvc_crt), in its debug build under debug.
    # Debug info is /Z7, in the object itself: /Zi has every parallel
    # cl.exe write one shared PDB, which fails (C1041) without /FS or a
    # per-target /Fd. The linker's /DEBUG still produces the final PDB.
    MSVC_VARIANTS: dict[str, tuple[list[str], list[str]]] = {
        "debug": (["/Od", "/Z7"], ["DEBUG", "_DEBUG"]),
        "release": (["/O2"], ["NDEBUG"]),
        # /O2 is already "maximize speed"; /Ob3 (VS 2019+) adds the more
        # aggressive inlining it leaves out. Nothing that changes results.
        "release-fastest": (["/O2", "/Ob3"], ["NDEBUG"]),
        "relwithdebinfo": (["/O2", "/Z7"], ["NDEBUG"]),
        "minsizerel": (["/O1"], ["NDEBUG"]),
    }

    # The flag selecting each C runtime (msvc_crt).
    MSVC_CRT_FLAGS: ClassVar[dict[str, str]] = {
        "dll": "/MD",
        "dll_dbg": "/MDd",
        "static": "/MT",
        "static_dbg": "/MTd",
    }

    def _cxx_standard_flag(self, standard: int) -> str:
        # MSVC switches are /std:c++14, c++17, c++20, and c++latest — there is
        # no /std:c++23 or :c++26, so anything above 20 uses c++latest.
        if standard >= 23:
            return "/std:c++latest"
        if standard <= 14:
            return "/std:c++14"
        return f"/std:c++{standard}"

    def make_target_preset(self, cross: Any) -> Preset:
        """Raise: these toolchains target only Windows; CPU selection is
        set_target_arch's job."""
        name = getattr(cross, "name", cross)
        raise PconsValueError(
            f"Cross preset '{name}' does not apply to the {self.name} toolchain: "
            f"it targets only Windows. To select the CPU, use "
            f'env.set_target_arch(...) (e.g. "arm64") instead.'
        )

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Add /MACHINE:xxx to linker and librarian."""
        machine = self.MSVC_MACHINE_MAP.get(arch.lower(), arch.upper())
        flag = f"/MACHINE:{machine}"
        return [
            ToolContribution("link", flags=(flag,)),
            ToolContribution("lib", flags=(flag,)),
        ]

    def _runtime_contributions(
        self, linkage: str, variant: str, target: Platform
    ) -> list[ToolContribution] | None:
        """The C runtime's flag on each compile tool. Each object names its
        runtime, so the link needs nothing."""
        flag = self.MSVC_CRT_FLAGS[msvc_crt(linkage, variant)]
        return [
            ToolContribution(tool, flags=(flag,))
            for tool in self._feature_preset_tools()
        ]

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        spec = self.MSVC_VARIANTS.get(variant.lower())
        if spec is None:
            raise PconsValueError(
                f"Unknown variant '{variant}'. "
                f"Supported variants: {', '.join(self.MSVC_VARIANTS)}."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        # Realized on the same compile tools as feature presets, so an
        # MSVC-style Fortran compiler (ifx's fc) gets working variants too.
        contribs = [
            ToolContribution(tool, flags=tuple(flags), defines=tuple(defines))
            for tool in self._feature_preset_tools()
        ]
        if "/Z7" in flags:
            contribs.append(ToolContribution("link", flags=("/DEBUG",)))
        return contribs


def module_definition(target: Target, symbols: Sequence[str]) -> str:
    """The ``.def`` file text exporting *symbols*, for an MSVC-ABI link.

    MSVC exports by exact name, so a ``*`` pattern can't be honored and
    is refused rather than silently exporting nothing.
    """
    patterns = [s for s in symbols if "*" in s or "?" in s]
    if patterns:
        raise PconsValueError(
            f"Target '{target.name}': MSVC exports symbols by exact name, "
            f"so the pattern {patterns[0]!r} in exported_symbols can't be "
            f"honored. List the names."
        )
    return "EXPORTS\n" + "".join(f"    {s}\n" for s in symbols)
