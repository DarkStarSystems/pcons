# SPDX-License-Identifier: MIT
"""WASI (WebAssembly System Interface) toolchain using wasi-sdk.

Provides a clang-based toolchain for compiling C/C++ to WebAssembly
targeting the WASI runtime interface. The output `.wasm` files can be
run with any WASI-compatible runtime (wasmtime, wasmer, WasmEdge, etc.).

wasi-sdk is a clang/LLVM distribution pre-configured for the wasm32-wasi
target, bundling wasi-libc and a matching sysroot.

Detection order for wasi-sdk:
1. ``WASI_SDK_PATH`` environment variable
2. Common install locations (``/opt/wasi-sdk``, ``~/.local/share/wasi-sdk``,
   Homebrew prefix)
3. Bare ``clang`` in PATH with wasm32 target support (advanced / manual setup)
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pcons.core.builder import CommandBuilder
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.unix import UnixToolchain
from pcons.tools.tool import BaseTool

if TYPE_CHECKING:
    from pcons.core.builder import Builder
    from pcons.core.environment import Environment
    from pcons.core.toolconfig import ToolConfig
    from pcons.tools.toolchain import SourceHandler

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


# =============================================================================
# Tools
# =============================================================================


class WasiCCompiler(BaseTool):
    """Clang C compiler targeting wasm32-wasi."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cc.cmd",
                "--target=wasm32-wasi",
                "$cc.sysroot_flag",
                "$cc.flags",
                "${prefix(cc.iprefix, cc.includes)}",
                "${prefix(cc.dprefix, cc.defines)}",
                "$cc.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
            # Placeholder — set during configure
            "sysroot_flag": "",
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object",
                "cc",
                "objcmd",
                src_suffixes=[".c"],
                target_suffixes=[".o"],
                language="c",
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        # Prefer wasi-sdk's clang, fall back to system clang
        sdk = find_wasi_sdk()
        if sdk:
            clang = config.find_program("clang", hints=[sdk / "bin"])
        else:
            clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cc", cmd=str(clang.path))
        if clang.version:
            tool_config.version = clang.version
        return tool_config


class WasiCxxCompiler(BaseTool):
    """Clang C++ compiler targeting wasm32-wasi."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "clang++",
            "flags": [],
            "iprefix": "-I",
            "includes": [],
            "dprefix": "-D",
            "defines": [],
            "depflags": ["-MD", "-MF", TargetPath(suffix=".d")],
            "objcmd": [
                "$cxx.cmd",
                "--target=wasm32-wasi",
                "$cxx.sysroot_flag",
                "$cxx.flags",
                "${prefix(cxx.iprefix, cxx.includes)}",
                "${prefix(cxx.dprefix, cxx.defines)}",
                "$cxx.depflags",
                "-c",
                "-o",
                TargetPath(),
                SourcePath(),
            ],
            "sysroot_flag": "",
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Object": CommandBuilder(
                "Object",
                "cxx",
                "objcmd",
                src_suffixes=[".cpp", ".cxx", ".cc", ".C"],
                target_suffixes=[".o"],
                language="cxx",
                single_source=True,
                depfile=TargetPath(suffix=".d"),
                deps_style="gcc",
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        sdk = find_wasi_sdk()
        if sdk:
            clangxx = config.find_program("clang++", hints=[sdk / "bin"])
        else:
            clangxx = config.find_program("clang++")
        if clangxx is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        tool_config = ToolConfig("cxx", cmd=str(clangxx.path))
        if clangxx.version:
            tool_config.version = clangxx.version
        return tool_config


class WasiArchiver(BaseTool):
    """LLVM archiver for wasm object files."""

    def __init__(self) -> None:
        super().__init__("ar")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "llvm-ar",
            "flags": ["rcs"],
            "libcmd": ["$ar.cmd", "$ar.flags", TargetPath(), SourcePath()],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "StaticLibrary": CommandBuilder(
                "StaticLibrary",
                "ar",
                "libcmd",
                src_suffixes=[".o"],
                target_suffixes=[".a"],
                single_source=False,
            ),
        }

    def configure(self, config: object) -> ToolConfig | None:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        sdk = find_wasi_sdk()
        if sdk:
            ar = config.find_program("llvm-ar", hints=[sdk / "bin"])
        else:
            ar = config.find_program("llvm-ar")
        if ar is None:
            ar = config.find_program("ar")
        if ar is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("ar", cmd=str(ar.path))


class WasiLinker(BaseTool):
    """Clang linker targeting wasm32-wasi.

    Links wasm object files into a ``.wasm`` executable.
    Shared libraries are not supported by WASI; attempting to build one
    will raise an error at build-description time.
    """

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
                "$link.sysroot_flag",
                "$link.flags",
                "-o",
                TargetPath(),
                SourcePath(),
                "${prefix(link.Lprefix, link.libdirs)}",
                "${prefix(link.lprefix, link.libs)}",
            ],
            "sysroot_flag": "",
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
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return None
        sdk = find_wasi_sdk()
        if sdk:
            clang = config.find_program("clang", hints=[sdk / "bin"])
        else:
            clang = config.find_program("clang")
        if clang is None:
            return None
        from pcons.core.toolconfig import ToolConfig

        return ToolConfig("link", cmd=str(clang.path))


# =============================================================================
# Toolchain
# =============================================================================


class WasiToolchain(UnixToolchain):
    """WASI toolchain for compiling C/C++ to WebAssembly.

    Uses wasi-sdk (a clang/LLVM distribution targeting wasm32-wasi).
    Produces ``.wasm`` executables that run on any WASI-compatible runtime.

    Shared libraries are not supported — WASI does not yet have a stable
    dynamic-linking ABI.  Calling ``SharedLibrary()`` with this toolchain
    will raise ``NotImplementedError``.
    """

    def __init__(self) -> None:
        super().__init__("wasi")
        self._sdk_path: Path | None = None
        self._sysroot: Path | None = None

    # -- Suffix / naming overrides ------------------------------------------

    def get_program_name(self, name: str) -> str:
        return f"{name}.wasm"

    def get_shared_library_name(self, name: str) -> str:
        raise NotImplementedError(
            "WASI does not support shared libraries. "
            "Use StaticLibrary instead, or target a native platform."
        )

    def get_compile_flags_for_target_type(self, target_type: str) -> list[str]:
        # No -fPIC needed for WebAssembly
        if target_type == "shared_library":
            raise NotImplementedError("WASI does not support shared libraries.")
        return []

    # -- Source handler ------------------------------------------------------

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        """Handle C/C++ sources (no Objective-C or assembly for wasm)."""
        from pcons.tools.toolchain import SourceHandler

        depfile = TargetPath(suffix=".d")
        # Check case-sensitive .C (C++ on Unix) before lowering
        if suffix == ".C":
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", depfile, "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc", ".c++"):
            return SourceHandler("cxx", "cxx", ".o", depfile, "gcc")
        return None

    # -- Configuration -------------------------------------------------------

    def _configure_tools(self, config: object) -> bool:
        from pcons.configure.config import Configure

        if not isinstance(config, Configure):
            return False

        # Locate SDK and sysroot
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
        """Set up tools and inject sysroot/SDK paths into the environment.

        If the SDK wasn't detected during configure (e.g. when created
        via the toolchain registry shortcut), detect it now.
        """
        # Lazy SDK detection — needed when created via registry
        if self._sdk_path is None:
            self._sdk_path = find_wasi_sdk()
        if self._sdk_path and self._sysroot is None:
            self._sysroot = _find_sysroot(self._sdk_path)

        super().setup(env)

        if self._sdk_path:
            bin_dir = self._sdk_path / "bin"
            # Point compiler/linker at wasi-sdk's clang
            for tool_name in ("cc", "link"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "cmd"):
                        tool.cmd = str(bin_dir / "clang")
            if env.has_tool("cxx"):
                env.cxx.cmd = str(bin_dir / "clang++")
            if env.has_tool("ar"):
                ar_path = bin_dir / "llvm-ar"
                if ar_path.exists():
                    env.ar.cmd = str(ar_path)

        if self._sysroot:
            sysroot_flag = f"--sysroot={self._sysroot}"
            for tool_name in ("cc", "cxx", "link"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "sysroot_flag"):
                        tool.sysroot_flag = sysroot_flag

    # -- Variant / arch overrides -------------------------------------------

    def apply_target_arch(self, env: Environment, arch: str, **kwargs: Any) -> None:
        # wasm32 is the only architecture; ignore arch requests
        super(UnixToolchain, self).apply_target_arch(env, "wasm32", **kwargs)

    def apply_cross_preset(self, env: Environment, preset: Any) -> None:
        # Sysroot is already handled by setup(); just apply extra flags
        if hasattr(preset, "extra_compile_flags") and preset.extra_compile_flags:
            for tool_name in ("cc", "cxx"):
                if env.has_tool(tool_name):
                    tool = getattr(env, tool_name)
                    if hasattr(tool, "flags") and isinstance(tool.flags, list):
                        tool.flags.extend(preset.extra_compile_flags)
        if hasattr(preset, "extra_link_flags") and preset.extra_link_flags:
            if env.has_tool("link"):
                if isinstance(env.link.flags, list):
                    env.link.flags.extend(preset.extra_link_flags)


# =============================================================================
# Finder
# =============================================================================


def find_wasi_toolchain() -> WasiToolchain:
    """Find and return a configured WASI toolchain.

    Returns:
        A configured WasiToolchain ready for use.

    Raises:
        RuntimeError: If wasi-sdk is not installed.
    """
    from pcons.tools.toolchain import toolchain_registry

    toolchain = toolchain_registry.find_available("wasm", ["wasi"])
    if toolchain is not None:
        return toolchain  # type: ignore[return-value]

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
    tool_classes=[WasiCCompiler, WasiCxxCompiler, WasiArchiver, WasiLinker],
    category="wasm",
    platforms=["linux", "darwin"],
    description="WASI SDK for standalone WebAssembly (.wasm)",
    finder="find_wasi_toolchain()",
)
