# SPDX-License-Identifier: MIT
"""Cross-compilation presets for common target platforms.

Presets configure sysroot, target triple, architecture flags, and SDK paths
for building on a different platform. Use with env.apply_cross_preset().

Example:
    from pcons.toolchains.presets import android, ios, wasm, linux_cross

    env.apply_cross_preset(android(ndk="~/android-ndk", arch="arm64-v8a"))
    env.apply_cross_preset(ios(arch="arm64"))
    env.apply_cross_preset(wasm(emsdk="~/emsdk"))
    env.apply_cross_preset(linux_cross(triple="aarch64-linux-gnu"))
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CrossPreset:
    """Describes a cross-compilation target.

    Attributes:
        name: Human-readable preset name (e.g., "android-arm64-v8a").
        arch: Target architecture (e.g., "arm64", "x86_64").
        triple: Compiler target triple (e.g., "aarch64-linux-android21").
        sysroot: Path to the target sysroot.
        sdk_path: Path to the SDK root (e.g., iOS SDK).
        extra_compile_flags: Additional compile flags for this target.
        extra_link_flags: Additional link flags for this target.
        env_vars: Environment variable overrides (e.g., CC, CXX commands).
    """

    name: str
    arch: str
    triple: str | None = None
    sysroot: str | None = None
    sdk_path: str | None = None
    extra_compile_flags: tuple[str, ...] = ()
    extra_link_flags: tuple[str, ...] = ()
    env_vars: dict[str, str] = field(default_factory=dict)


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
        sdk_path=str(ndk_path),
        env_vars={
            "CC": str(bin_dir / f"{triple}-clang"),
            "CXX": str(bin_dir / f"{triple}-clang++"),
        },
    )


def ios(
    arch: str = "arm64",
    *,
    min_version: str = "15.0",
    sdk: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for iOS.

    Args:
        arch: Target architecture ("arm64" or "x86_64" for simulator).
        min_version: Minimum iOS deployment target.
        sdk: Path to iOS SDK. If None, auto-detected via xcrun.

    Returns:
        CrossPreset configured for iOS.
    """
    is_simulator = arch == "x86_64"

    if is_simulator:
        triple = f"{arch}-apple-ios{min_version}-simulator"
    else:
        triple = f"{arch}-apple-ios{min_version}"

    compile_flags = [f"-mios-version-min={min_version}"]

    # SDK path can be resolved at configure time via xcrun
    sysroot = sdk

    return CrossPreset(
        name=f"ios-{arch}",
        arch=arch,
        triple=triple,
        sysroot=sysroot,
        extra_compile_flags=tuple(compile_flags),
    )


def wasm(
    emsdk: str | None = None,
) -> CrossPreset:
    """Create a cross-compilation preset for WebAssembly via Emscripten.

    Args:
        emsdk: Path to the Emscripten SDK root. If None, assumes emcc
               is already in PATH.

    Returns:
        CrossPreset configured for WebAssembly.
    """
    env_vars: dict[str, str] = {}

    if emsdk:
        emsdk_path = Path(emsdk).expanduser()
        upstream = emsdk_path / "upstream" / "emscripten"
        env_vars["CC"] = str(upstream / "emcc")
        env_vars["CXX"] = str(upstream / "em++")
    else:
        env_vars["CC"] = "emcc"
        env_vars["CXX"] = "em++"

    return CrossPreset(
        name="wasm32",
        arch="wasm32",
        triple="wasm32-unknown-emscripten",
        env_vars=env_vars,
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
    # Extract architecture from triple
    arch = triple.split("-")[0]

    return CrossPreset(
        name=f"linux-{arch}",
        arch=arch,
        triple=triple,
        sysroot=sysroot,
    )
