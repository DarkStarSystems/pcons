# SPDX-License-Identifier: MIT
"""Cross-compilation presets for common target platforms.

Presets configure sysroot, target triple, architecture flags, and SDK paths
for building on a different platform. Use with env.apply_cross_preset().

Example:
    from pcons.toolchains.presets import android, ios, emscripten, wasi_sdk, linux_cross

    env.apply_cross_preset(android(ndk="~/android-ndk", arch="arm64-v8a"))
    env.apply_cross_preset(ios(arch="arm64"))
    env.apply_cross_preset(emscripten(emsdk="~/emsdk"))
    env.apply_cross_preset(linux_cross(triple="aarch64-linux-gnu"))
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path

# Deprecated env_vars keys mapped to pcons tool names (see CrossPreset).
_ENV_VAR_TOOL_MAP: dict[str, str] = {
    "CC": "cc",
    "CXX": "cxx",
    "LD": "link",
    "AR": "ar",
}


@dataclass(frozen=True)
class CrossPreset:
    """Describes a cross-compilation target.

    Attributes:
        name: Human-readable preset name (e.g., "android-arm64-v8a").
        arch: Target CPU name in the target ecosystem's own vocabulary
              (e.g., "arm64", "arm64-v8a", "wasm32"). Metadata only —
              never a flag source; the triple encodes the CPU.
        triple: Compiler target triple (e.g., "aarch64-linux-android21").
        sysroot: Root of the target's headers/libraries (sysroot, SDK).
        extra_compile_flags: Additional compile flags for this target.
        extra_link_flags: Additional link flags for this target.
        tool_cmds: Per-tool command overrides keyed by pcons tool name
                   ("cc", "cxx", "link", "ar", ...) — the binary-retarget
                   mechanism (docs/presets.md).
        env_vars: Deprecated alias for tool_cmds using environment-variable
                  vocabulary (CC→cc, CXX→cxx, LD→link, AR→ar). tool_cmds
                  wins on conflict.
    """

    name: str
    arch: str
    triple: str | None = None
    sysroot: str | None = None
    extra_compile_flags: tuple[str, ...] = ()
    extra_link_flags: tuple[str, ...] = ()
    tool_cmds: dict[str, str] = field(default_factory=dict)
    env_vars: dict[str, str] = field(default_factory=dict)

    def resolved_tool_cmds(self) -> dict[str, str]:
        """tool_cmds merged with the deprecated env_vars aliases."""
        cmds: dict[str, str] = {}
        for var, value in self.env_vars.items():
            tool = _ENV_VAR_TOOL_MAP.get(var.upper())
            if tool is not None:
                cmds[tool] = value
        cmds.update(self.tool_cmds)
        return cmds


def android(
    ndk: str,
    arch: str = "arm64-v8a",
    api: int = 21,
) -> CrossPreset:
    """Create a cross-compilation preset for Android NDK.

    Args:
        ndk: Path to the Android NDK root directory.
        arch: Android architecture name. Supported values:
              "arm64-v8a", "armeabi-v7a", "x86_64", "x86".
        api: Minimum Android API level (default: 21).

    Returns:
        CrossPreset configured for Android.
    """
    triple_map = {
        "arm64-v8a": "aarch64-linux-android",
        "armeabi-v7a": "armv7a-linux-androideabi",
        "x86_64": "x86_64-linux-android",
        "x86": "i686-linux-android",
    }
    if arch not in triple_map:
        raise ValueError(
            f"Unknown Android architecture '{arch}'. Supported: {', '.join(triple_map)}"
        )

    triple = f"{triple_map[arch]}{api}"
    ndk_path = Path(ndk).expanduser()

    # Detect host platform for NDK prebuilt path
    host_system = platform.system().lower()
    host_arch = platform.machine()
    if host_system == "darwin":
        host_tag = "darwin-x86_64"
    elif host_system == "linux":
        host_tag = f"linux-{host_arch}"
    else:
        host_tag = "windows-x86_64"

    toolchain_dir = ndk_path / "toolchains" / "llvm" / "prebuilt" / host_tag
    sysroot = str(toolchain_dir / "sysroot")
    bin_dir = toolchain_dir / "bin"

    return CrossPreset(
        name=f"android-{arch}",
        arch=arch,
        triple=triple,
        sysroot=sysroot,
        tool_cmds={
            "cc": str(bin_dir / f"{triple}-clang"),
            "cxx": str(bin_dir / f"{triple}-clang++"),
            "link": str(bin_dir / f"{triple}-clang++"),
            "ar": str(bin_dir / "llvm-ar"),
        },
    )


def ios(
    arch: str = "arm64",
    *,
    min_version: str = "15.0",
    sdk: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for iOS.

    Works with any Apple-aware toolchain: Swift, and LLVM/clang for
    C/C++/Objective-C++.

    Args:
        arch: Target architecture ("arm64" or "x86_64" for simulator).
        min_version: Minimum iOS deployment target.
        sdk: Path to iOS SDK. If None, the toolchain resolves it via
             xcrun when the preset is applied.

    Returns:
        CrossPreset configured for iOS.
    """
    is_simulator = arch == "x86_64"

    if is_simulator:
        triple = f"{arch}-apple-ios{min_version}-simulator"
    else:
        triple = f"{arch}-apple-ios{min_version}"

    compile_flags = [f"-mios-version-min={min_version}"]

    return CrossPreset(
        name=f"ios-{arch}",
        arch=arch,
        triple=triple,
        sysroot=sdk,
        extra_compile_flags=tuple(compile_flags),
    )


def emscripten(
    emsdk: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for WebAssembly via Emscripten.

    Apply to the dedicated Emscripten toolchain
    (``project.Environment(toolchain="emscripten")``), which owns output
    suffixes, shared-library rules, and the link driver; applying a wasm
    preset to a native toolchain raises. For WASI targets use
    ``toolchain="wasi"`` and :func:`wasi_sdk`.

    Args:
        emsdk: Path to the Emscripten SDK root. If None, assumes emcc
               is already in PATH.

    Returns:
        CrossPreset configured for Emscripten WebAssembly.
    """
    tool_cmds: dict[str, str] = {}

    if emsdk:
        emsdk_path = Path(emsdk).expanduser()
        upstream = emsdk_path / "upstream" / "emscripten"
        tool_cmds["cc"] = str(upstream / "emcc")
        tool_cmds["cxx"] = str(upstream / "em++")
    else:
        tool_cmds["cc"] = "emcc"
        tool_cmds["cxx"] = "em++"

    return CrossPreset(
        name="wasm32-emscripten",
        arch="wasm32",
        triple="wasm32-unknown-emscripten",
        tool_cmds=tool_cmds,
    )


# PEP 783 PyEmscripten ABIs: platform tag -> CPython version it targets.
PYEMSCRIPTEN_ABIS: dict[str, str] = {
    "2025_0": "3.13",  # Pyodide 0.29.x
    "2026_0": "3.14",  # Pyodide 314.x
}


def pyodide(
    abi: str = "2026_0",
    emsdk: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for Pyodide / PEP 783 PyEmscripten.

    Builds on :func:`emscripten` and adds the flags needed to produce a CPython
    extension module as an Emscripten *side module* — the form Pyodide loads and
    the ABI standardized by `PEP 783 <https://peps.python.org/pep-0783/>`_. The
    resulting wheels can be published to PyPI under the ``pyemscripten_*``
    platform tags.

    pcons covers the C/C++ compile + link side. Full wheel packaging (ABI tags,
    metadata, PyPI upload) is the job of ``pyodide-build`` / ``cibuildwheel``;
    point them at this toolchain for the build step.

    Args:
        abi: PyEmscripten ABI: ``"2025_0"`` (CPython 3.13, Pyodide 0.29.x) or
             ``"2026_0"`` (CPython 3.14, Pyodide 314.x).
        emsdk: Path to the Emscripten SDK root. If None, assumes ``emcc`` is on
               PATH. The ABI is tied to a specific Emscripten version, which the
               SDK must match.

    Returns:
        CrossPreset configured for the PyEmscripten side-module ABI.
    """
    if abi not in PYEMSCRIPTEN_ABIS:
        raise ValueError(
            f"Unknown PyEmscripten ABI '{abi}'. Supported: "
            f"{', '.join(PYEMSCRIPTEN_ABIS)}"
        )

    base = emscripten(emsdk=emsdk)
    return CrossPreset(
        name=f"pyemscripten_{abi}",
        arch=base.arch,
        triple=base.triple,
        sysroot=base.sysroot,
        # Extension modules are position-independent Emscripten side modules.
        extra_compile_flags=base.extra_compile_flags + ("-fPIC",),
        extra_link_flags=base.extra_link_flags + ("-sSIDE_MODULE=1",),
        tool_cmds=base.tool_cmds,
    )


def wasi_sdk(
    sdk_path: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for WASI via wasi-sdk.

    Apply to the dedicated WASI toolchain
    (``project.Environment(toolchain="wasi")``); applying a wasm preset to
    a native toolchain raises (docs/presets.md).

    Args:
        sdk_path: Path to the wasi-sdk root. If None, auto-detected
                  via ``WASI_SDK_PATH`` or common install locations.

    Returns:
        CrossPreset configured for wasm32-wasi.
    """
    from pcons.toolchains.wasi import find_wasi_sdk

    sysroot: str | None = None
    tool_cmds: dict[str, str] = {}

    if sdk_path:
        p = Path(sdk_path).expanduser()
    else:
        p = find_wasi_sdk()

    if p is not None:
        bin_dir = p / "bin"
        sysroot_dir = p / "share" / "wasi-sysroot"
        if not sysroot_dir.is_dir():
            sysroot_dir = p / "wasi-sysroot"
        if sysroot_dir.is_dir():
            sysroot = str(sysroot_dir)
        tool_cmds["cc"] = str(bin_dir / "clang")
        tool_cmds["cxx"] = str(bin_dir / "clang++")

    return CrossPreset(
        name="wasm32-wasi",
        arch="wasm32",
        triple="wasm32-wasi",
        sysroot=sysroot,
        tool_cmds=tool_cmds,
    )


def linux_cross(
    triple: str,
    sysroot: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for Linux targets.

    Args:
        triple: GCC/Clang target triple (e.g., "aarch64-linux-gnu",
                "arm-linux-gnueabihf", "riscv64-linux-gnu").
        sysroot: Path to the target sysroot. If None, relies on
                 the toolchain's default sysroot.

    Returns:
        CrossPreset configured for Linux cross-compilation.
    """
    arch = triple.split("-")[0]

    return CrossPreset(
        name=f"linux-{arch}",
        arch=arch,
        triple=triple,
        sysroot=sysroot,
    )
