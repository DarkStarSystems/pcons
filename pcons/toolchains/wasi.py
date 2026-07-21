# SPDX-License-Identifier: MIT
"""WASI (WebAssembly System Interface) toolchain using wasi-sdk.

wasi-sdk is a clang/LLVM distribution pre-configured for the wasm32-wasi
target, bundling wasi-libc and a matching sysroot. The output ``.wasm``
files run on any WASI-compatible runtime (wasmtime, wasmer, WasmEdge).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder import CommandBuilder
from pcons.core.preset import Preset, ToolContribution
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


def find_wasi_sdk() -> Path | None:
    """Locate a wasi-sdk installation.

    Checks, in order:
    1. ``WASI_SDK_PATH`` environment variable
    2. ``/opt/wasi-sdk``
    3. ``~/.local/share/wasi-sdk``
    4. Homebrew prefix (``$(brew --prefix)/share/wasi-sdk``)

    Returns:
        Path to the wasi-sdk root, or None if not found.
    """
    # 1. Environment variable
    env_path = os.environ.get("WASI_SDK_PATH")
    if env_path:
        p = Path(env_path).expanduser()
        if _is_wasi_sdk(p):
            return p

    # 2. Common install locations
    candidates = [
        Path("/opt/wasi-sdk"),
        Path.home() / ".local" / "share" / "wasi-sdk",
    ]

    # 3. Homebrew
    brew = shutil.which("brew")
    if brew:
        import subprocess

        try:
            result = subprocess.run(
                [brew, "--prefix"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                prefix = result.stdout.strip()
                candidates.append(Path(prefix) / "share" / "wasi-sdk")
        except (subprocess.TimeoutExpired, OSError):
            pass

    for candidate in candidates:
        if _is_wasi_sdk(candidate):
            return candidate

    return None


def _is_wasi_sdk(path: Path) -> bool:
    """Check whether *path* looks like a wasi-sdk installation."""
    if not path.is_dir():
        return False
    bin_dir = path / "bin"
    sysroot = _find_sysroot(path)
    return bin_dir.is_dir() and sysroot is not None


def _find_sysroot(sdk_path: Path) -> Path | None:
    """Find the WASI sysroot within a wasi-sdk installation."""
    # wasi-sdk >= 20 puts the sysroot at share/wasi-sysroot
    candidate = sdk_path / "share" / "wasi-sysroot"
    if candidate.is_dir():
        return candidate
    # Older versions may have it directly
    candidate = sdk_path / "wasi-sysroot"
    if candidate.is_dir():
        return candidate
    return None


def _wasi_hints() -> list[Path | str] | None:
    """Search hints pointing at a wasi-sdk's bin directory, if one is found."""
    sdk = find_wasi_sdk()
    return [sdk / "bin"] if sdk else None


def is_wasi_sdk_available() -> bool:
    """Check whether a real wasi-sdk installation is available.

    A bare ``clang`` on PATH is not sufficient — WASI requires wasi-sdk's
    sysroot and wasm32-wasi-aware clang, not just any system compiler.
    """
    return find_wasi_sdk() is not None


# =============================================================================
# Tools
# =============================================================================


class WasiCCompiler(BaseTool):
    """Clang C compiler targeting wasm32-wasi."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars(
            "clang",
            "cc",
            target_tokens=["--target=wasm32-wasi"],
        )

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cc", object_suffix=".o")

    def configure(self, config: object) -> ToolConfig | None:
        # Prefer wasi-sdk's clang, fall back to system clang
        return self._find_tool_config(
            config, "clang", hints=_wasi_hints(), with_version=True
        )


class WasiCxxCompiler(BaseTool):
    """Clang C++ compiler targeting wasm32-wasi."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return gnu_compile_vars(
            "clang++",
            "cxx",
            target_tokens=["--target=wasm32-wasi"],
        )

    def builders(self) -> dict[str, Builder]:
        return gnu_compile_builders("cxx", object_suffix=".o")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(
            config, "clang++", hints=_wasi_hints(), with_version=True
        )


class WasiArchiver(BaseTool):
    """LLVM archiver for wasm object files."""

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return gnu_archiver_vars("llvm-ar")

    def builders(self) -> dict[str, Builder]:
        return gnu_archiver_builders(object_suffix=".o", static_lib_suffix=".a")

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "llvm-ar", "ar", hints=_wasi_hints())


class WasiLinker(BaseTool):
    """Clang linker targeting wasm32-wasi: objects into a ``.wasm`` executable."""

    def __init__(self) -> None:
        super().__init__("link")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang",
            "flags": [],
            "lprefix": "-l",
            "libs": [],
            "Lprefix": "-L",
            "libdirs": [],
            "progcmd": [
                "$link.cmd",
                "--target=wasm32-wasi",
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Program": CommandBuilder(
                "Program",
                "link",
                "progcmd",
                src_suffixes=[".o"],
                target_suffixes=[".wasm"],
                single_source=False,
            ),
            # No SharedLibrary builder — WASI does not support dynamic linking.
        }

    def configure(self, config: object) -> ToolConfig | None:
        return self._find_tool_config(config, "clang", hints=_wasi_hints())


# =============================================================================
# Toolchain
# =============================================================================


class WasiToolchain(WasmToolchain):
    """WASI toolchain for compiling C/C++ to WebAssembly.

    Uses wasi-sdk (a clang/LLVM distribution targeting wasm32-wasi).
    Produces ``.wasm`` executables that run on any WASI-compatible runtime.

    Shared libraries are not supported — WASI does not yet have a stable
    dynamic-linking ABI.  Calling ``SharedLibrary()`` with this toolchain
    will raise ``NotImplementedError``.
    """

    TOOL_NAMES = ("cc", "cxx", "ar", "link")

    program_suffix = ".wasm"
    platform_label = "WASI"

    def __init__(self) -> None:
        super().__init__("wasi")
        self._sdk_path: Path | None = None
        self._sysroot: Path | None = None

    # -- Configuration -------------------------------------------------------

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        self._sdk_path = find_wasi_sdk()
        if self._sdk_path:
            self._sysroot = _find_sysroot(self._sdk_path)
        else:
            logger.info("wasi-sdk not found; WASI toolchain unavailable")
            return False

        cc = WasiCCompiler()
        if cc.configure(config) is None:
            return False

        cxx = WasiCxxCompiler()
        cxx.configure(config)

        ar = WasiArchiver()
        ar.configure(config)

        link = WasiLinker()
        if link.configure(config) is None:
            return False

        self._tools = {"cc": cc, "cxx": cxx, "ar": ar, "link": link}
        return True

    def setup(self, env: Environment) -> None:
        """Set up tools, detecting the SDK lazily if configure didn't.

        SDK wiring (tool commands, sysroot) is declared via setup_presets()
        so explain() attributes it to the "wasi-sdk" preset.
        """
        # Lazy SDK detection — needed when created via registry
        if self._sdk_path is None:
            self._sdk_path = find_wasi_sdk()
        if self._sdk_path and self._sysroot is None:
            self._sysroot = _find_sysroot(self._sdk_path)

        super().setup(env)

    def setup_presets(self, env: Environment) -> list[Preset]:
        """Declare SDK tool commands and sysroot as an attributable preset."""
        contribs: list[ToolContribution] = []
        if self._sdk_path:
            bin_dir = self._sdk_path / "bin"
            contribs.append(ToolContribution("cc", cmd=str(bin_dir / "clang")))
            contribs.append(ToolContribution("cxx", cmd=str(bin_dir / "clang++")))
            contribs.append(ToolContribution("link", cmd=str(bin_dir / "clang")))
            ar_path = bin_dir / "llvm-ar"
            if ar_path.exists():
                contribs.append(ToolContribution("ar", cmd=str(ar_path)))
        if self._sysroot:
            sysroot_flag = f"--sysroot={self._sysroot}"
            for tool_name in ("cc", "cxx", "link"):
                contribs.append(ToolContribution(tool_name, flags=(sysroot_flag,)))
        if not contribs:
            return []
        return [
            Preset(name="wasi-sdk", category="toolchain", contributions=tuple(contribs))
        ]


# =============================================================================
# Finder
# =============================================================================


def find_wasi_toolchain() -> WasiToolchain:
    """Return a configured WASI toolchain, or raise RuntimeError."""
    from pcons.tools.toolchain import toolchain_registry

    toolchain = toolchain_registry.find_available("wasm", ["wasi"])
    if isinstance(toolchain, WasiToolchain):
        return toolchain

    raise RuntimeError(
        "wasi-sdk not found. Install it from https://github.com/WebAssembly/wasi-sdk "
        "and either set WASI_SDK_PATH or install to /opt/wasi-sdk. "
        "On macOS: brew install wasi-sdk"
    )


# =============================================================================
# Registration
# =============================================================================

from pcons.tools.toolchain import toolchain_registry  # noqa: E402

toolchain_registry.register(
    WasiToolchain,
    aliases=["wasi", "wasi-sdk"],
    check_command="clang",
    is_available=is_wasi_sdk_available,
    tool_classes=[WasiCCompiler, WasiCxxCompiler, WasiArchiver, WasiLinker],
    category="wasm",
    platforms=["linux", "darwin"],
    description="WASI SDK for standalone WebAssembly (.wasm)",
    finder="find_wasi_toolchain()",
)


toolchain_registry.register_finder(
    ["wasi"],
    find_wasi_toolchain,
    description="Auto-detect the WASI SDK WebAssembly toolchain",
)
