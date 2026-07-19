# SPDX-License-Identifier: MIT
"""Swift toolchain: whole-module compilation with swiftc.

Swift's compilation unit is the module, not the file: every file in a
module sees the others without imports. pcons maps one Target to one
Swift module — all of a target's ``.swift`` sources compile in a single
whole-module (``-wmo``) invocation producing one object file and one
``.swiftmodule`` (via ``SourceHandler.group_sources``). Importing
another target's module works through the ``.swiftmodule`` search path;
no dyndep scanner is needed (unlike Fortran): inter-module ordering
falls out of ordinary target dependencies, and intra-module ordering is
handled by whole-module compilation.

The module name is the target name sanitized to a Swift identifier.
Library targets compile with ``-parse-as-library`` (top-level code is
only meaningful in programs); the program entry point is top-level code
in ``main.swift`` or an ``@main`` type, as usual for Swift.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from pcons.configure.platform import get_platform
from pcons.core.builder import CommandBuilder
from pcons.core.preset import ToolContribution
from pcons.core.subst import PathToken, SourcePath, TargetPath
from pcons.toolchains.gcc import GccArchiver
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import SourceHandler, toolchain_registry

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.target import Target
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.tool import Builder

SWIFT_EXTENSIONS = frozenset({".swift"})

# Where each target's .swiftmodule lands, relative to the build dir.
# Shared so dependents get a single -I search path.
SWIFTMODULE_DIR = "swiftmodules"


def module_name_for(target_name: str) -> str:
    """Sanitize a target name into a valid Swift module identifier."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", target_name)
    if not name or name[0].isdigit():
        name = f"_{name}"
    return name


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

    Variables:
        cmd: Compiler command (default: 'swiftc')
        flags: General compiler flags (list)
        iprefix/includes: .swiftmodule (and header) search directories
        dprefix/defines: Conditional-compilation flags (swiftc -D NAME)
        depflags: Dependency-file generation flags
        objcmd: Whole-module compile command template. MODULE_NAME,
                MODULE_PATH, and MODULE_FLAGS are per-node variables
                provided by SwiftToolchain.setup_group_node().
    """

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
                "-module-name",
                "$MODULE_NAME",
                "$MODULE_FLAGS",
                "-emit-module",
                "-emit-module-path",
                "$MODULE_PATH",
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

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "swiftc", with_version=True)


class SwiftLinker(BaseTool):
    """Swift linker tool: swiftc as the link driver.

    swiftc locates the Swift runtime libraries itself, so linking
    Swift objects (and any C/C++ objects mixed in) needs no manual
    runtime paths.
    """

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

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "swiftc", with_version=True)


class SwiftToolchain(UnixToolchain):
    """Swift toolchain using swiftc for compilation and linking.

    One pcons Target = one Swift module, compiled whole-module. The
    ``.swiftmodule`` for each target is emitted into a shared
    ``swiftmodules/`` directory in the build tree; library targets
    propagate it to dependents via ``public.include_dirs`` so their
    ``import Foo`` resolves.
    """

    TOOL_NAMES = ("swiftc", "ar", "link")

    # swiftc enables most diagnostics by default; "warnings" is an
    # intentional no-op so generic scripts can apply it everywhere.
    FEATURE_PRESETS = {
        "warnings": {"compile_flags": []},
        "werror": {"compile_flags": ["-warnings-as-errors"]},
    }

    SWIFT_VARIANTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "debug": (("-Onone", "-g"), ("DEBUG",)),
        "release": (("-O",), ()),
        "relwithdebinfo": (("-O", "-g"), ()),
        "minsizerel": (("-Osize",), ()),
    }

    def __init__(self) -> None:
        super().__init__("swift")

    @property
    def language_priority(self) -> dict[str, int]:
        # Swift wins linker selection over C/C++ when it's the primary
        # toolchain: swiftc-as-linker handles the Swift runtime.
        return {**self.DEFAULT_LANGUAGE_PRIORITY, "swift": 3}

    def _feature_preset_tools(self) -> tuple[str, ...]:
        return ("swiftc",)

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

    def _configure_tools(self, config: object) -> bool:
        compiler = SwiftCompiler()
        archiver = GccArchiver()
        linker = SwiftLinker()

        compiler_config = compiler.configure(config)
        if compiler_config is None:
            return False
        archiver.configure(config)
        linker.configure(config)

        self._tools = {"swiftc": compiler, "ar": archiver, "link": linker}
        return True

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
        module_path = target.build_dir / SWIFTMODULE_DIR / f"{module_name}.swiftmodule"

        # Programs may contain top-level code (the entry point); library
        # targets must not, and need -parse-as-library.
        module_flags: list[str] = []
        if target.target_type != "program":
            module_flags.append("-parse-as-library")

        node._build_info["vars"] = {
            "MODULE_NAME": module_name,
            # path_type="build" paths are given relative to the build dir
            "MODULE_PATH": PathToken(
                path=f"{SWIFTMODULE_DIR}/{module_name}.swiftmodule",
                path_type="build",
            ),
            "MODULE_FLAGS": module_flags,
        }
        # Declare the .swiftmodule as an implicit output so Ninja knows
        # this build produces it (dependents' compiles can depend on it).
        node._build_info["outputs"] = {
            "primary": {"path": node.path, "suffix": node.path.suffix},
            "swiftmodule": {
                "path": module_path,
                "suffix": ".swiftmodule",
                "implicit": True,
            },
        }

        # Dependents' compile steps need the module search path and an
        # ordering edge on the .swiftmodule file. Record it on the target;
        # consumed below for this target's dependencies.
        module_node = target.project.node(module_path)
        target._builder_data["swiftmodule_node"] = module_node

        # Library targets propagate the shared swiftmodules/ dir so
        # dependents' `import Foo` resolves — ordinary usage requirements
        # carry it to their swiftc.includes.
        if target.target_type != "program":
            module_search_dir = target.build_dir / SWIFTMODULE_DIR
            if module_search_dir not in target.public.include_dirs:
                target.public.include_dirs.append(module_search_dir)

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
    tool_classes=[SwiftCompiler, GccArchiver, SwiftLinker],
    category="swift",
    platforms=["darwin", "linux"],
    description="Swift compiler (whole-module compilation, swiftc links)",
    finder="find_swift_toolchain()",
)

toolchain_registry.register_finder(
    ["swift"],
    find_swift_toolchain,
    description="Auto-detect the Swift toolchain (swiftc)",
)
