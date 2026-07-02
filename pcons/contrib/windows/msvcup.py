# SPDX-License-Identifier: MIT
"""MSVC toolchain installation via msvcup.

Downloads and installs the MSVC compiler and Windows SDK without
requiring Visual Studio. Uses msvcup's autoenv to generate standalone
wrapper executables (cl.exe, link.exe, etc.) that work from any prompt.

Usage:
    from pcons.contrib.windows.msvcup import ensure_msvc

    # In pcons-build.py, before find_c_toolchain():
    ensure_msvc("14.44.17.14", "10.0.22621.7")
    toolchain = find_c_toolchain()  # Finds cl.exe from msvcup

See https://github.com/marler8997/msvcup for more information.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from pcons.configure.platform import get_platform

logger = logging.getLogger(__name__)

# SHA-256 checksums for the pinned msvcup release archives, keyed by
# (msvcup_version, host_arch). These are the checksums GitHub computes
# automatically for each uploaded release asset -- not something msvcup
# itself publishes -- but they let us pin the exact bytes we expect for a
# given release and refuse to run anything else (e.g. a MITM'd or
# compromised download).
#
# To update when bumping the default `msvcup_version`, fetch the digest
# for each windows zip asset, e.g.:
#   gh release view <tag> --repo marler8997/msvcup --json assets \
#     --jq '.assets[] | select(.name | endswith("windows.zip")) | "\(.name) \(.digest)"'
# or, without `gh`:
#   curl -sL <asset-url> | sha256sum
_RELEASE_SHA256: dict[tuple[str, str], str] = {
    ("v2026_02_07", "x86_64"): (
        "fdc1743a766ee96ebb912b3cd657f4cc2bc773a125887f4a05242230d0554124"
    ),
    ("v2026_02_07", "aarch64"): (
        "53192e26737f450c8b97ab949069fb96cc017e90230aa7c11e7dc2e47127b90c"
    ),
}


class MsvcUp:
    """Manage MSVC toolchain installation via msvcup.

    Downloads and installs the MSVC compiler and Windows SDK
    without requiring Visual Studio. Uses autoenv to make tools
    available in PATH.

    Example::

        from pcons.contrib.windows.msvcup import ensure_msvc

        # In pcons-build.py, before find_c_toolchain():
        ensure_msvc("14.44.17.14", "10.0.22621.7")
        toolchain = find_c_toolchain()  # Finds cl.exe from msvcup
    """

    # msvcup hardcodes C:\msvcup as its install directory (no override flag).
    # We use the same path for bootstrap (storing msvcup.exe itself) and for
    # knowing where autoenv output goes.
    MSVCUP_DIR = r"C:\msvcup"
    RELEASE_URL = (
        "https://github.com/marler8997/msvcup/releases/download"
        "/{version}/msvcup-{host_arch}-windows.zip"
    )

    # Map pcons platform.arch -> msvcup host arch for download URL
    _HOST_ARCH_MAP: dict[str, str] = {
        "x86_64": "x86_64",
        "arm64": "aarch64",
    }

    # Map pcons platform.arch -> msvcup --target-cpu value
    _TARGET_CPU_MAP: dict[str, str] = {
        "x86_64": "x64",
        "arm64": "arm64",
    }

    _MANIFEST_UPDATE_VALUES = ("off", "daily", "always")

    def __init__(
        self,
        msvc_version: str,
        sdk_version: str,
        *,
        target_cpu: str | None = None,
        msvcup_version: str = "v2026_02_07",
        lock_file: str | Path | None = None,
        manifest_update: str = "off",
    ) -> None:
        if manifest_update not in self._MANIFEST_UPDATE_VALUES:
            msg = (
                f"manifest_update must be one of {self._MANIFEST_UPDATE_VALUES}, "
                f"got {manifest_update!r}"
            )
            raise ValueError(msg)
        self._msvc_version = msvc_version
        self._sdk_version = sdk_version
        self._target_cpu = target_cpu
        self._msvcup_version = msvcup_version
        self._lock_file = Path(lock_file) if lock_file is not None else None
        self._manifest_update = manifest_update

    def ensure_installed(self) -> Path:
        """Ensure msvcup and the specified toolchain are installed.

        Returns the autoenv directory (containing cl.exe, link.exe, etc.).
        Also prepends it to PATH so ``find_c_toolchain()`` discovers the tools.
        """
        msvcup_exe = self._bootstrap_msvcup()
        self._run_install(msvcup_exe)
        autoenv_dir = self._run_autoenv(msvcup_exe)
        self._add_to_path(autoenv_dir)
        return autoenv_dir

    # -- Architecture helpers -------------------------------------------------

    def _resolve_target_cpu(self) -> str:
        """Resolve target_cpu from host architecture if not explicitly set."""
        if self._target_cpu is not None:
            return self._target_cpu
        arch = get_platform().arch
        cpu = self._TARGET_CPU_MAP.get(arch)
        if cpu is None:
            msg = f"Unsupported host architecture for msvcup: {arch}"
            raise RuntimeError(msg)
        return cpu

    def _resolve_host_arch(self) -> str:
        """Resolve host architecture for the msvcup download URL."""
        arch = get_platform().arch
        host_arch = self._HOST_ARCH_MAP.get(arch)
        if host_arch is None:
            msg = f"No msvcup binary available for architecture: {arch}"
            raise RuntimeError(msg)
        return host_arch

    # -- Bootstrap ------------------------------------------------------------

    def _bootstrap_msvcup(self) -> Path:
        """Download msvcup.exe if not already present."""
        msvcup_dir = Path(self.MSVCUP_DIR)
        msvcup_exe = msvcup_dir / "bin" / "msvcup.exe"
        if msvcup_exe.exists():
            return msvcup_exe

        host_arch = self._resolve_host_arch()
        zip_url = self.RELEASE_URL.format(
            version=self._msvcup_version,
            host_arch=host_arch,
        )
        cache_dir = msvcup_dir / "cache"
        zip_path = cache_dir / f"msvcup-{self._msvcup_version}.zip"

        try:
            zip_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            msg = (
                f"Cannot create {msvcup_dir}. msvcup requires write access "
                r"to C:\msvcup. You may need to run once with admin privileges, "
                r"or pre-create C:\msvcup with appropriate permissions."
            )
            raise PermissionError(msg) from None

        logger.info("Downloading msvcup %s for %s...", self._msvcup_version, host_arch)
        urllib.request.urlretrieve(zip_url, zip_path)  # noqa: S310
        self._verify_checksum(zip_path, host_arch)

        msvcup_exe.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            # The zip contains msvcup.exe at the top level
            for name in zf.namelist():
                if name.endswith("msvcup.exe"):
                    with zf.open(name) as src, open(msvcup_exe, "wb") as dst:
                        dst.write(src.read())
                    break
            else:
                msg = f"msvcup.exe not found in {zip_path}"
                raise FileNotFoundError(msg)

        logger.info("Installed msvcup to %s", msvcup_exe)
        return msvcup_exe

    def _verify_checksum(self, zip_path: Path, host_arch: str) -> None:
        """Verify a downloaded archive against its pinned SHA-256 checksum.

        Fails closed: if no checksum is pinned for this version/arch, or the
        computed digest doesn't match, the downloaded file is deleted and a
        ``RuntimeError`` is raised rather than extracting and executing an
        unverified binary.
        """
        expected = _RELEASE_SHA256.get((self._msvcup_version, host_arch))
        if expected is None:
            zip_path.unlink(missing_ok=True)
            msg = (
                f"No pinned SHA-256 checksum for msvcup {self._msvcup_version} "
                f"({host_arch}); refusing to run an unverified download. "
                "Add an entry to _RELEASE_SHA256 in msvcup.py -- see the "
                "comment above that dict for how to get the hash."
            )
            raise RuntimeError(msg)

        digest = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()

        if actual != expected:
            zip_path.unlink(missing_ok=True)
            msg = (
                f"msvcup download checksum mismatch for {self._msvcup_version} "
                f"({host_arch}): expected {expected}, got {actual}. The "
                "downloaded file has been deleted. This may indicate a "
                "corrupted download or a tampered/compromised source -- "
                "refusing to execute it."
            )
            raise RuntimeError(msg)

    # -- Install + autoenv ----------------------------------------------------

    def _run_install(self, msvcup_exe: Path) -> None:
        """Run msvcup install with specified versions."""
        lock_file = self._lock_file or Path(self.MSVCUP_DIR) / "msvcup.lock"
        cmd = [
            str(msvcup_exe),
            "install",
            "--lock-file",
            str(lock_file),
            f"--manifest-update-{self._manifest_update}",
        ]
        cmd.extend(
            [
                f"msvc-{self._msvc_version}",
                f"sdk-{self._sdk_version}",
            ]
        )
        logger.info(
            "Installing MSVC %s + SDK %s...", self._msvc_version, self._sdk_version
        )
        subprocess.run(cmd, check=True)

    def _run_autoenv(self, msvcup_exe: Path) -> Path:
        """Run msvcup autoenv to generate wrapper executables."""
        target_cpu = self._resolve_target_cpu()
        msvcup_dir = Path(self.MSVCUP_DIR)
        autoenv_dir = msvcup_dir / f"autoenv-{target_cpu}"
        cmd = [
            str(msvcup_exe),
            "autoenv",
            "--target-cpu",
            target_cpu,
            "--out-dir",
            str(autoenv_dir),
            f"msvc-{self._msvc_version}",
            f"sdk-{self._sdk_version}",
        ]
        logger.info("Setting up autoenv for %s...", target_cpu)
        subprocess.run(cmd, check=True)
        return autoenv_dir

    # -- PATH -----------------------------------------------------------------

    def _add_to_path(self, autoenv_dir: Path) -> None:
        """Prepend autoenv directory to PATH."""
        current_path = os.environ.get("PATH", "")
        autoenv_str = str(autoenv_dir)
        if autoenv_str not in current_path:
            os.environ["PATH"] = autoenv_str + os.pathsep + current_path
            logger.info("Added %s to PATH", autoenv_dir)


def ensure_msvc(
    msvc_version: str,
    sdk_version: str,
    *,
    target_cpu: str | None = None,
    msvcup_version: str = "v2026_02_07",
    lock_file: str | Path | None = None,
    manifest_update: str = "off",
) -> Path:
    r"""Ensure MSVC toolchain is installed and in PATH.

    Call this before ``find_c_toolchain()`` in your ``pcons-build.py``.

    Note: msvcup installs to ``C:\msvcup`` (hardcoded in msvcup itself).
    On most Windows systems this is writable by regular users, since the
    default ACLs on ``C:\`` allow authenticated users to create new
    directories.  In restricted environments, admin access may be needed
    for the first run.

    Args:
        msvc_version: MSVC version (e.g., ``"14.44.17.14"``).
        sdk_version: Windows SDK version (e.g., ``"10.0.22621.7"``).
        target_cpu: Target CPU architecture.  Auto-detected from host
            if not specified (x64 on x86_64, arm64 on arm64).
            Can be set explicitly for cross-compilation.
        msvcup_version: msvcup release tag (default: latest known).
        lock_file: Path to lock file for reproducible installs.
        manifest_update: Manifest update policy: ``"off"`` (default),
            ``"daily"``, or ``"always"``.

    Returns:
        Path to autoenv directory containing wrapper executables.

    Raises:
        PermissionError: If ``C:\msvcup`` cannot be created.
    """
    if sys.platform != "win32":
        logger.debug("msvcup: not on Windows, skipping")
        return Path()

    up = MsvcUp(
        msvc_version,
        sdk_version,
        target_cpu=target_cpu,
        msvcup_version=msvcup_version,
        lock_file=lock_file,
        manifest_update=manifest_update,
    )
    return up.ensure_installed()
