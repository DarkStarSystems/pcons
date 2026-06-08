# SPDX-License-Identifier: MIT
"""Emscripten toolchain for compiling C/C++ to WebAssembly + JavaScript.

Provides an ``emcc``/``em++`` based toolchain for compiling C/C++ to
WebAssembly targeting the browser or Node.js.  Unlike the WASI toolchain
which produces standalone ``.wasm`` files, Emscripten linking produces
**two files**: a ``.js`` loader and a companion ``.wasm`` module.  The
``.js`` file is the entry point (``node out.js``).

Detection order for emsdk:
1. ``EMSDK`` environment variable
2. Common install locations (``~/emsdk``, ``/opt/emsdk``,
   ``~/.local/share/emsdk``)
3. Bare ``emcc`` already on PATH (user has activated emsdk)
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pcons.core.builder import MultiOutputBuilder, OutputSpec
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.gnu_common import (
    gnu_archiver_builders,
    gnu_archiver_vars,
    gnu_compile_builders,
    gnu_compile_vars,
)
from pcons.toolchains.wasm_common import WasmToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig

logger = logging.getLogger(__name__)


# =============================================================================
# SDK Discovery
# =============================================================================


def find_emsdk() -> Path | None:
    """Locate an Emscripten SDK installation.

    Checks, in order:
    1. ``EMSDK`` environment variable
    2. ``~/emsdk``
    3. ``/opt/emsdk``
    4. ``~/.local/share/emsdk``
    5. Bare ``emcc`` on PATH (already activated)

    Returns:
        Path to the emsdk root, or None if not found.
    """
    # 1. Environment variable
    env_path = os.environ.get("EMSDK")
    if env_path:
        p = Path(env_path).expanduser()
        if _is_emsdk(p):
            return p

    # 2. Common install locations
    candidates = [
        Path.home() / "emsdk",
        Path("/opt/emsdk"),
        Path.home() / ".local" / "share" / "emsdk",
    ]
    for candidate in candidates:
        if _is_emsdk(candidate):
            return candidate

    # 3. Bare emcc on PATH — user already activated emsdk
    emcc = shutil.which("emcc")
    if emcc:
        # Return None for the SDK path; tools will use PATH directly
        return None

    return None


def _is_emsdk(path: Path) -> bool:
    """Check whether *path* looks like an Emscripten SDK root."""
    if not path.is_dir():
        return False
    # emsdk has an emcc inside upstream/emscripten/
    emcc = path / "upstream" / "emscripten" / "emcc"
    emcc_bat = path / "upstream" / "emscripten" / "emcc.bat"
    return emcc.exists() or emcc_bat.exists()


def _find_emcc_dir(emsdk_path: Path) -> Path | None:
    """Find the directory containing emcc within an emsdk installation."""
    candidate = emsdk_path / "upstream" / "emscripten"
    if (candidate / "emcc").exists() or (candidate / "emcc.bat").exists():
        return candidate
    return None


def _emsdk_hints() -> list[Path | str] | None:
    """Search hints pointing at an emsdk's emcc directory, if one is found."""
    emsdk = find_emsdk()
    if not emsdk:
        return None
    emcc_dir = _find_emcc_dir(emsdk)
    return [emcc_dir] if emcc_dir else None


def is_emcc_available() -> bool:
    """Check whether emcc is available (either via emsdk or PATH)."""
    env_path = os.environ.get("EMSDK")
    if env_path:
        p = Path(env_path).expanduser()
        if _is_emsdk(p):
            return True
    return shutil.which("emcc") is not None


# =============================================================================
# Tools
# =============================================================================


class EmccCCompiler(BaseTool):
    """Emscripten C compiler (emcc)."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars("emcc", "cc")

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cc", object_suffix=".o")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(
            config, "emcc", hints=_emsdk_hints(), with_version=True
        )


class EmccCxxCompiler(BaseTool):
    """Emscripten C++ compiler (em++)."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars("em++", "cxx")

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cxx", object_suffix=".o")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(
            config, "em++", hints=_emsdk_hints(), with_version=True
        )


class EmccArchiver(BaseTool):
    """Emscripten archiver (emar)."""

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return gnu_archiver_vars("emar")

    def builders(self) -> dict[str, Builder]:
        return gnu_archiver_builders(object_suffix=".o", static_lib_suffix=".a")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "emar", hints=_emsdk_hints())


class EmccLinker(BaseTool):
    """Emscripten linker (emcc).

    Links wasm object files into a ``.js`` + ``.wasm`` pair.
    The ``.js`` file is the primary output (entry point that loads the wasm).

    Supports ``-s`` settings via ``env.link.settings`` list and
    ``env.link.sprefix`` (defaults to ``-s``).

    Shared libraries are not supported — Emscripten's dynamic linking
    (SIDE_MODULE) is niche.  Calling ``SharedLibrary()`` will raise an error.
    """

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "emcc",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "sprefix": "-s",
            "settings": [],
            "progcmd": [
                "$link.cmd",
                "$link.flags",
                "-o",
                TargetPath(index=0),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
                "${prefix(link.sprefix, link.settings)}",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": MultiOutputBuilder(
                "Program",
                "link",
                "progcmd",
                outputs=[
                    OutputSpec("primary", ".js"),
                    OutputSpec("wasm", ".wasm"),
                ],
                src_suffixes=[".o"],
                single_source=False,
            ),
            # No SharedLibrary builder — Emscripten SIDE_MODULE is niche.
        }

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "emcc", hints=_emsdk_hints())


# =============================================================================
# Toolchain
# =============================================================================


class EmscriptenToolchain(WasmToolchain):
    """Emscripten toolchain for compiling C/C++ to WebAssembly + JavaScript.

    Uses Emscripten (emcc/em++) to produce ``.js`` + ``.wasm`` pairs.
    The ``.js`` file is the entry point that loads the ``.wasm`` module;
    run with ``node out.js`` or include in a web page.

    Shared libraries are not supported — Emscripten's dynamic linking
    (``SIDE_MODULE``) is niche.  Calling ``SharedLibrary()`` with this
    toolchain will raise ``NotImplementedError``.
    """

    TOOL_NAMES = ("cc", "cxx", "ar", "link")

    program_suffix = ".js"
    platform_label = "Emscripten"

    def __init__(self) -> None:
        super().__init__("emscripten")
        self._emsdk_path: Path | None = None

    # -- Configuration -------------------------------------------------------

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        # Check that emcc is available
        if not is_emcc_available():
            logger.info("Emscripten (emcc) not found; toolchain unavailable")
            return False

        cc = EmccCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = EmccCxxCompiler()
        cxx.configure(config)

        ar = EmccArchiver()
        ar.configure(config)

        link = EmccLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}
        return True

    def setup(self, env: Environment) -> None:
        """Set up tools and inject emsdk paths into the environment.

        If the SDK wasn't detected during configure (e.g. when created
        via the toolchain registry shortcut), detect it now.
        """
        # Lazy SDK detection — needed when created via registry
        if self._emsdk_path is None:
            self._emsdk_path = find_emsdk()

        super().setup(env)

        if self._emsdk_path:
            emcc_dir = _find_emcc_dir(self._emsdk_path)
            if emcc_dir:
                # Point compiler/linker at emsdk's emcc
                for tool_name in ("cc", "link"):
                    if env.has_tool(tool_name):
                        tool = getattr(env, tool_name)
                        if hasattr(tool, "cmd"):
                            tool.cmd = str(emcc_dir / "emcc")
                if env.has_tool("cxx"):
                    env.cxx.cmd = str(emcc_dir / "em++")
                if env.has_tool("ar"):
                    env.ar.cmd = str(emcc_dir / "emar")


# =============================================================================
# Finder
# =============================================================================


def find_emscripten_toolchain() -> EmscriptenToolchain:
    """Find and return a configured Emscripten toolchain.

    Returns:
        A configured EmscriptenToolchain ready for use.

    Raises:
        RuntimeError: If Emscripten is not installed.
    """
    from pcons.tools.toolchain import toolchain_registry

    toolchain = toolchain_registry.find_available("wasm", ["emscripten", "emcc"])
    if toolchain is not None:
        return cast(EmscriptenToolchain, toolchain)

    raise RuntimeError(
        "Emscripten not found. Install it from https://emscripten.org/docs/getting_started/ "
        "and either set EMSDK or activate the emsdk environment. "
        "On macOS: brew install emscripten"
    )


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    EmscriptenToolchain,
    aliases=["emscripten", "emcc"],
    check_command="emcc",
    tool_classes=[EmccCCompiler, EmccCxxCompiler, EmccArchiver, EmccLinker],
    category="wasm",
    platforms=["linux", "darwin"],
    description="Emscripten C/C++ to WebAssembly + JS (browser/Node.js)",
    finder="find_emscripten_toolchain()",
)
