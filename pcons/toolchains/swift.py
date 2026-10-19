# SPDX-License-Identifier: MIT
"""Swift toolchain: whole-module compilation with swiftc.

Swift's compilation unit is the module, not the file. pcons maps one
Target to one Swift module: all of a target's ``.swift`` sources compile
in a single whole-module (``-wmo``) invocation producing one object file
and one ``.swiftmodule``. Imports resolve through the ``.swiftmodule``
search path, so no dyndep scanner is needed — inter-module ordering
falls out of ordinary target dependencies.

The module name is the target name sanitized to a Swift identifier.
Library targets compile with ``-parse-as-library``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.preset import Preset, ToolContribution
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import LinkNeeds, SourceHandler, toolchain_registry
from pcons.util.macos import apple_sdk_for_triple

if TYPE_CHECKING:
    from pcons.configure.platform import Platform
    from pcons.core.environment import Environment
    from pcons.core.target import Target
    from pcons.tools.tool import Builder

logger = logging.getLogger(__name__)

SWIFT_EXTENSIONS = frozenset({".swift"})

# Where each target's .swiftmodule lands, relative to the build dir.
# Shared so dependents get a single -I search path.
SWIFTMODULE_DIR = "swiftmodules"


def _swift_set_cxx_interop(env: Environment, standard: str | int | None = None) -> None:
    """``env.swiftc.set_cxx_interop(...)`` — enable Swift/C++ interoperability.

    Turns on ``-cxx-interoperability-mode=default`` so Swift code can import
    C++ (and ``-emit-clang-header`` output exposes C++-callable declarations).
    An optional C++ standard (``"c++20"`` or ``20``) is passed through to the
    clang importer via ``-Xcc -std=...``.
    """
    env._apply_setting("swiftc.cxx_interop", standard)


def module_name_for(target_name: str) -> str:
    """Sanitize a target name into a valid Swift module identifier."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", target_name)
    if not name or name[0].isdigit():
        name = f"_{name}"
    return name


def clang_module_map(
    project: Any, name: str, headers: list[str | Path] | tuple[str | Path, ...]
) -> Path:
    """Generate a ``module.modulemap`` exposing C headers to Swift.

    Writes ``<build_dir>/modulemaps/<name>/module.modulemap`` (only when
    its content changes, so builds stay incremental) and returns the
    directory. Append it to the C library's ``public.include_dirs`` and
    dependent Swift code can ``import <name>``:

        cstats = project.StaticLibrary("cstats", env, sources=[...])
        cstats.public.include_dirs.append("cstats/include")
        cstats.public.include_dirs.append(
            clang_module_map(project, "CStats", ["cstats/include/cstats.h"])
        )

    Header paths are read from the calling script's directory, as
    ``sources=`` reads them, and written absolute inside the map, so the
    generated file works regardless of where swiftc runs. The map goes in
    that script's build directory. A hand-written module.modulemap shipped
    in the include dir works just as well.
    """
    map_dir = project.build_dir / "modulemaps" / name
    lines = [f"module {name} {{"]
    for header in headers:
        p = project.top.root_dir / project._script_path(header)
        lines.append(f'    header "{p}"')
    lines.append("    export *")
    lines.append("}")
    content = "\n".join(lines) + "\n"

    map_dir.mkdir(parents=True, exist_ok=True)
    map_file = map_dir / "module.modulemap"
    if not map_file.exists() or map_file.read_text() != content:
        map_file.write_text(content)
    return map_dir


def _link_tail() -> list[object]:
    """Link-command tail; swiftc understands GNU-style -L/-l/-framework."""
    return [
        "-o",
        TargetPath(),
        SourcePath(),
        "${prefix(link.Lprefix, link.libdirs)}",
        "${prefix(link.lprefix, link.libs)}",
        "${prefix(link.Fprefix, link.frameworkdirs)}",
        "${pairwise(link.fprefix, link.frameworks)}",
    ]


class SwiftCompiler(BaseTool):
    """Swift compiler tool (whole-module compilation).

    objcmd's MODULE_NAME, MODULE_PATH, MODULE_FLAGS, and HEADER_FLAGS are
    per-node variables provided by SwiftToolchain.setup_group_node().
    """

    env_var = "SWIFTC"

    def __init__(self) -> None:
        super().__init__("swiftc", language="swift")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "swiftc",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            # When true, library modules also emit a C/C++ header
            # (<Module>-Swift.h) into swiftmodules/ so C++ can call Swift.
            "interop_header": False,
            # When true, library modules build with -enable-library-evolution
            # and emit a .swiftinterface (for distributable/resilient libs).
            "library_evolution": False,
            # Always passed: Swift 6 refuses to emit a .swiftinterface
            # unless the language mode is stated. "5" is swiftc's own default.
            "language_mode": "5",
            # -emit-dependencies-path is frontend-only; with -wmo there is
            # exactly one frontend job, so -Xfrontend passing is reliable.
            "depflags": [
                "-Xfrontend",
                "-emit-dependencies-path",
                "-Xfrontend",
                TargetPath(suffix=".d"),
            ],
            "objcmd": [
                "$swiftc.cmd",
                "-emit-object",
                "-wmo",
                "-swift-version",
                "$swiftc.language_mode",
                "-module-name",
                "$MODULE_NAME",
                "$MODULE_FLAGS",
                "-emit-module",
                "-emit-module-path",
                "$MODULE_PATH",
                "$HEADER_FLAGS",
                "$swiftc.depflags",
                "$swiftc.flags",
                "${prefix(swiftc.iprefix, swiftc.includes)}",
                "${prefix(swiftc.dprefix, swiftc.defines)}",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
        }

    def builders(self) -> dict[str, Builder]:
        platform = get_platform()
        return {
            "Object": CommandBuilder(
                "Object",
                "swiftc",
                "objcmd",
                src_suffixes=sorted(SWIFT_EXTENSIONS),
                target_suffixes=[platform.object_suffix],
                language="swift",
                single_source=False,  # whole-module: N sources -> 1 object
            ),
        }


class SwiftArchiver(GccArchiver):
    """Archiver for Swift static libraries.

    Plain ``ar`` everywhere except Windows, where ``llvm-ar`` is used —
    it ships with the swift.org toolchain, so it's always present next
    to swiftc (a GNU ar generally isn't).
    """

    def _archiver_cmd(self) -> str:
        return "llvm-ar" if get_platform().is_windows else "ar"

    def default_vars(self) -> dict[str, object]:
        vars = super().default_vars()
        vars["cmd"] = self._archiver_cmd()
        return vars


class SwiftLinker(BaseTool):
    """Swift linker tool: swiftc as the link driver.

    swiftc locates the Swift runtime libraries itself, so linking
    Swift objects (and any C/C++ objects mixed in) needs no manual
    runtime paths.
    """

    env_var = "SWIFTC"

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "swiftc",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "Fprefix": "-F",
            "frameworkdirs": [],
            "fprefix": "-framework",
            "frameworks": [],
            "progcmd": ["$link.cmd", "$link.flags", *_link_tail()],
            "sharedcmd": ["$link.cmd", "-emit-library", "$link.flags", *_link_tail()],
        }

    def builders(self) -> dict[str, Builder]:
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
            "SharedLibrary": CommandBuilder(
                "SharedLibrary",
                "link",
                "sharedcmd",
                src_suffixes=[platform.object_suffix],
                target_suffixes=[platform.shared_lib_suffix],
                single_source=False,
            ),
        }


class SwiftToolchain(UnixToolchain):
    """Swift toolchain using swiftc for compilation and linking.

    One pcons Target = one Swift module, compiled whole-module. The
    ``.swiftmodule`` for each target is emitted into a shared
    ``swiftmodules/`` directory in the build tree; library targets
    propagate it to dependents via ``public.include_dirs`` so their
    ``import Foo`` resolves.
    """

    TOOL_NAMES = ("swiftc", "ar", "link")

    # No profile, fast-math or openmp. warnings is a deliberate no-op (Swift
    # 6 has warning groups only), and so is pthread, which Glibc autolinks.
    FEATURE_PRESETS = {
        "warnings": {"compile_flags": []},
        # Bare -Werror takes a warning group.
        "werror": {"compile_flags": ["-warnings-as-errors"]},
        # "undefined" is unsupported on Linux.
        "sanitize": {
            "compile_flags": ["-sanitize=address"],
            "link_flags": ["-sanitize=address"],
        },
        "lto": {"compile_flags": ["-lto=llvm-full"], "link_flags": ["-lto=llvm-full"]},
        # Realized in make_feature_preset: the link flags are ELF's.
        "hardened": {},
        "pthread": {"compile_flags": []},
        # LLVM's coverage format, not gcov's.
        "coverage": {
            "compile_flags": ["-profile-generate", "-profile-coverage-mapping"],
            "link_flags": ["-profile-generate"],
        },
    }

    SWIFT_VARIANTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "debug": (("-Onone", "-g"), ("DEBUG",)),
        "release": (("-O",), ()),
        # -Ounchecked drops safety checks, which changes behavior, so the
        # fastest safe level is the same -O.
        "release-fastest": (("-O",), ()),
        "relwithdebinfo": (("-O", "-g"), ()),
        "minsizerel": (("-Osize",), ()),
    }

    def __init__(self) -> None:
        super().__init__("swift")
        self._target_info: dict[str, Any] | None = None

    LINK_DRIVER_TOOLS: ClassVar[dict[str, str]] = {"swift": "swiftc"}

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("swiftc",)

    def get_compile_flags_for_target_type(
        self,
        target_type: str,
        env: Environment | None = None,
        *,
        pic: bool | None = None,
    ) -> list[str]:
        """None: swiftc always generates position-independent code, and
        takes no ``-fPIC``."""
        return []

    STATIC_RUNTIME_LINK_FLAGS = ("-static-stdlib",)

    def _runtime_contributions(
        self, linkage: str, variant: str, target: Platform
    ) -> list[ToolContribution] | None:
        """Swift's runtime is part of Apple's systems, so it links static
        only elsewhere. libstdc++ stays shared even then: swiftc links it
        by name."""
        if linkage == "static" and target.is_apple:
            return None
        return super()._runtime_contributions(linkage, variant, target)

    def make_feature_preset(
        self, name: str, target: Platform | None = None
    ) -> Preset | None:
        """Realize feature presets; ``hardened`` follows the platform.

        Stack protection and PIE are swiftc's defaults, so what's left is
        RELRO and immediate binding on an ELF link; macOS needs nothing.
        Windows is unverified, so unavailable.
        """
        if name != "hardened":
            return super().make_feature_preset(name, target)
        platform = target or get_platform()
        if platform.is_windows:
            return None
        contribs: tuple[ToolContribution, ...] = ()
        if not platform.is_apple:
            relro_now = ("-Xlinker", "-z", "-Xlinker", "relro")
            relro_now += ("-Xlinker", "-z", "-Xlinker", "now")
            contribs = (ToolContribution("link", flags=relro_now),)
        return Preset(name=name, category="feature", contributions=contribs)

    def _arch_contributions(self, arch: str) -> list[ToolContribution]:
        """Raise: swiftc has no -arch; the CPU is part of its -target triple."""
        raise ValueError(
            f"{self.name} can't build for target arch '{arch}' by flag; use a "
            f"cross preset (swiftc takes a -target triple)."
        )

    def _sysroot_contributions(self, cross: Any) -> list[ToolContribution]:
        """None: swiftc takes the sysroot as ``-sdk`` (_target_contributions)."""
        return []

    def _variant_contributions(
        self, variant: str, **kwargs: Any
    ) -> list[ToolContribution]:
        spec = self.SWIFT_VARIANTS.get(variant.lower())
        if spec is None:
            raise ValueError(
                f"Unknown variant '{variant}'. "
                f"Supported variants: {', '.join(sorted(self.SWIFT_VARIANTS))}."
            )
        flags = list(spec[0]) + list(kwargs.get("extra_flags", []))
        defines = list(spec[1]) + list(kwargs.get("extra_defines", []))
        return [
            ToolContribution("swiftc", flags=tuple(flags), defines=tuple(defines)),
        ]

    def tool_setting(self, tool: str, name: str) -> Any:
        if tool == "swiftc" and name == "set_cxx_interop":
            return _swift_set_cxx_interop
        return super().tool_setting(tool, name)

    def make_setting_preset(self, name: str, *args: Any) -> Preset | None:
        if name == "swiftc.cxx_interop":
            return self.make_cxx_interop_preset(*args)
        return super().make_setting_preset(name, *args)

    def make_cxx_interop_preset(self, standard: str | int | None = None) -> Preset:
        """Realize C++-interop mode as a preset (visible to env.explain())."""
        flags: list[str] = ["-cxx-interoperability-mode=default"]
        if standard is not None:
            std = str(standard).strip().lower()
            if not std.startswith("c++"):
                std = f"c++{std}"
            flags.extend(["-Xcc", f"-std={std}"])
        return Preset(
            name="swift-cxx-interop",
            category="feature",
            contributions=(ToolContribution("swiftc", flags=tuple(flags)),),
        )

    #: The libraries every Swift object autolinks, which an ELF linker
    #: ignores (swiftc's driver extracts them). -Onone objects need
    #: swiftSwiftOnoneSupport too.
    LINUX_AUTOLINK_LIBS: ClassVar[tuple[str, ...]] = (
        "swiftCore",
        "swift_Concurrency",
        "swift_StringProcessing",
        "swift_RegexParser",
    )

    def link_needs(
        self,
        target: Target,
        languages: set[str],
        link_language: str,
        env: Environment,
    ) -> LinkNeeds:
        """Swift objects linked by a C or C++ driver need the Swift runtime,
        from the directories swiftc reports, found there at run time too.

        On Linux they also need ``swiftrt.o``, without which the runtime
        metadata is silently never registered (a cast to a protocol then
        fails), and the libraries their autolink entries list, which an
        ELF linker ignores. On macOS the objects autolink; below
        deployment target 26 the program finds the runtime through
        ``/usr/lib/swift``.
        """
        needs = super().link_needs(target, languages, link_language, env)
        if "swift" not in languages or link_language == "swift":
            return needs
        libdirs = tuple(self._runtime_libdirs())
        platform = self._target_platform(target)
        if platform.is_apple:
            return needs | LinkNeeds(
                libs=("swiftCore",), libdirs=libdirs, rpaths=("/usr/lib/swift",)
            )
        if platform.is_windows:
            return needs | LinkNeeds(libs=("swiftCore",), libdirs=libdirs)
        libs = self.LINUX_AUTOLINK_LIBS
        # -Onone is swiftc's default, so it's what a variant that doesn't
        # optimize (or none at all) compiles with.
        variant = self.SWIFT_VARIANTS.get(str(env.variant).lower())
        if variant is None or "-Onone" in variant[0]:
            libs = (*libs, "swiftSwiftOnoneSupport")
        swiftrt = self._swiftrt_object()
        return needs | LinkNeeds(
            libs=libs,
            libdirs=libdirs,
            rpaths=libdirs,
            objects=(swiftrt,) if swiftrt else (),
        )

    def _target_info_json(self) -> dict[str, Any]:
        """``swiftc -print-target-info``, asked once."""
        if self._target_info is None:
            import json
            import subprocess

            self._target_info = {}
            try:
                out = subprocess.run(
                    ["swiftc", "-print-target-info"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                ).stdout
                self._target_info = json.loads(out)
            except (OSError, subprocess.SubprocessError, ValueError):
                logger.warning(
                    "Could not query swiftc -print-target-info for the Swift "
                    "runtime; mixed C++/Swift links may need it set manually"
                )
        return self._target_info

    def _runtime_libdirs(self) -> list[str]:
        paths = self._target_info_json().get("paths", {})
        return list(paths.get("runtimeLibraryPaths", []))

    def _swiftrt_object(self) -> str | None:
        """``<runtimeResourcePath>/linux/<arch>/swiftrt.o``, if swiftc says."""
        info = self._target_info_json()
        resource = info.get("paths", {}).get("runtimeResourcePath")
        arch = info.get("target", {}).get("arch")
        if not resource or not arch:
            return None
        return str(PurePosixPath(resource) / "linux" / arch / "swiftrt.o")

    def _target_contributions(self, cross: Any) -> list[ToolContribution]:
        """Add swiftc/link -target and -sdk for cross targets (e.g. ios()).

        Swift embeds the deployment version in the triple, and Apple
        targets need the matching SDK; when the CrossPreset carries no
        sysroot it is resolved via xcrun.
        """
        contribs = super()._target_contributions(cross)
        triple = getattr(cross, "triple", None)
        if not triple:
            return contribs
        # swiftc drives this toolchain's link and rejects clang-style
        # -arch/--target/--sysroot link flags; the Swift -target triple
        # carries the architecture, so replace the base link contributions
        # with swiftc-style ones. (Base cc/cxx contributions are inert —
        # this toolchain declares no cc/cxx tools.)
        contribs = [c for c in contribs if c.tool != "link"]
        swift_flags: list[str] = ["-target", str(triple)]
        sdk = getattr(cross, "sysroot", None) or apple_sdk_for_triple(str(triple))
        if sdk:
            swift_flags += ["-sdk", str(sdk)]
        contribs.append(ToolContribution("swiftc", flags=tuple(swift_flags)))
        contribs.append(ToolContribution("link", flags=tuple(swift_flags)))
        return contribs

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        if suffix in SWIFT_EXTENSIONS:
            platform = get_platform()
            return SourceHandler(
                tool_name="swiftc",
                language="swift",
                object_suffix=platform.object_suffix,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
                group_sources=True,
            )
        return super().get_source_handler(suffix)

    def setup_group_node(self, node: Any, target: Target, env: Environment) -> None:
        """Provide per-module template vars and declare the .swiftmodule output."""
        module_name = module_name_for(target.name)
        module_dir = target.build_dir / SWIFTMODULE_DIR
        module_path = module_dir / f"{module_name}.swiftmodule"
        root = target.project.top.root_dir

        def build_token(path: Path) -> PathToken:
            # The declared output's own path, which the generator writes as
            # swiftc, running in the build directory, sees it.
            return PathToken(path=root / path)

        # Programs may contain top-level code (the entry point); library
        # targets must not, and need -parse-as-library.
        is_library = target.target_type != "program"
        module_flags: list[object] = []
        interface_path = None
        if is_library:
            module_flags.append("-parse-as-library")
            # Built for Windows, the .swiftmodule records linkage: without
            # -static, importers emit __imp_ (dllimport) references and fail
            # to link against a static library.
            if target.target_type == "static_library" and env.target.is_windows:
                module_flags.append("-static")
            if bool(getattr(env.swiftc, "library_evolution", False)):
                interface_path = module_dir / f"{module_name}.swiftinterface"
                module_flags += [
                    "-enable-library-evolution",
                    "-emit-module-interface-path",
                    build_token(interface_path),
                    # The interface-verify pass runs as an extra frontend job
                    # that inherits our -Xfrontend depfile flags and rejects
                    # them ("this mode does not support emitting dependency
                    # files"); skip it — the interface is still emitted.
                    "-no-verify-emitted-module-interface",
                ]

        # Optional C/C++ interop header (<Module>-Swift.h) for library
        # modules, emitted next to the .swiftmodule so the same propagated
        # include dir serves both `import Foo` and `#include "Foo-Swift.h"`.
        header_flags: list[object] = []
        header_path = None
        emit_header = bool(getattr(env.swiftc, "interop_header", False))
        if emit_header and is_library:
            header_path = module_dir / f"{module_name}-Swift.h"
            header_flags = ["-emit-clang-header-path", build_token(header_path)]

        node._build_info["vars"] = {
            "MODULE_NAME": module_name,
            "MODULE_PATH": build_token(module_path),
            "MODULE_FLAGS": module_flags,
            "HEADER_FLAGS": header_flags,
        }
        # Declare the .swiftmodule (and interop header) as implicit outputs
        # so Ninja knows this build produces them (dependents' compiles can
        # depend on them).
        node._build_info["outputs"] = {
            "primary": {"path": node.path, "suffix": node.path.suffix},
            "swiftmodule": {
                "path": module_path,
                "suffix": ".swiftmodule",
                "implicit": True,
            },
        }
        if interface_path is not None:
            node._build_info["outputs"]["swiftinterface"] = {
                "path": interface_path,
                "suffix": ".swiftinterface",
                "implicit": True,
            }
        if header_path is not None:
            node._build_info["outputs"]["clang_header"] = {
                "path": header_path,
                "suffix": ".h",
                "implicit": True,
            }
            # Adding the header to output_nodes routes it through the
            # mixed-outputs dependency channel: consumers' compile steps
            # gain an implicit dep on it (same mechanism as cargo+cbindgen
            # generated headers), so C++ that #includes it builds after it
            # exists.
            target.output_nodes.append(target.project._node(header_path))

        # Dependents' compile steps need the module search path and an
        # ordering edge on the .swiftmodule file. Record it on the target;
        # consumed below for this target's dependencies.
        module_node = target.project._node(module_path)
        target._builder_data["swiftmodule_node"] = module_node

        # Library targets propagate the shared swiftmodules/ dir so
        # dependents' `import Foo` resolves — ordinary usage requirements
        # carry it to their swiftc.includes.
        if target.target_type != "program":
            if module_dir not in target.public.include_dirs:
                target.public.include_dirs.append(
                    target.project.top.root_dir / module_dir
                )

        # Wire this compile against every dependency's .swiftmodule (deps
        # resolve before dependents, so their nodes exist by now).
        seen: set[int] = set()
        for dep in target.transitive_dependencies():
            if id(dep) in seen:
                continue
            seen.add(id(dep))
            dep_module = dep._builder_data.get("swiftmodule_node")
            if dep_module is not None:
                node.implicit_deps.append(dep_module)


def find_swift_toolchain(prefer: list[str] | None = None) -> SwiftToolchain:
    """Find and return a configured Swift toolchain.

    Returns:
        A configured SwiftToolchain ready for use.

    Raises:
        RuntimeError: If swiftc is not available.
    """
    toolchain = toolchain_registry.find_available("swift", prefer)
    if toolchain is not None:
        return cast("SwiftToolchain", toolchain)
    raise RuntimeError(
        "No Swift toolchain found: 'swiftc' is not in PATH. "
        "Install Xcode (macOS) or a swift.org toolchain (Linux)."
    )


toolchain_registry.register(
    SwiftToolchain,
    aliases=["swiftc"],
    check_command="swiftc",
    tool_classes=[SwiftCompiler, SwiftArchiver, SwiftLinker],
    category="swift",
    platforms=["darwin", "linux", "win32"],
    description="Swift compiler (whole-module compilation, swiftc links)",
    finder="find_swift_toolchain()",
)

toolchain_registry.register_finder(
    ["swift"],
    find_swift_toolchain,
    description="Auto-detect the Swift toolchain (swiftc)",
)
