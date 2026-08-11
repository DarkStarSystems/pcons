# SPDX-License-Identifier: MIT
"""Command-line interface for pcons-fetch.

Downloads and builds external dependencies from source, driven by a
deps.toml file.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from pcons import __version__
from pcons._cli_click import MergingGroup
from pcons.packages.description import PackageDescription

logger = logging.getLogger("pcons-fetch")


def load_deps_file(path: Path) -> dict[str, Any]:
    """Load a deps.toml file.

    Args:
        path: Path to the deps.toml file.

    Returns:
        Parsed TOML data.

    Raises:
        FileNotFoundError: If file doesn't exist.
        tomllib.TOMLDecodeError: If file is not valid TOML.
    """
    with open(path, "rb") as f:
        return tomllib.load(f)


def _split_git_url_and_ref(url: str) -> tuple[str, str | None]:
    """Split a git URL into repository URL and optional ref."""
    git_url = url[4:] if url.startswith("git+") else url
    parsed = urllib.parse.urlsplit(git_url)

    if parsed.scheme:
        at_pos = git_url.rfind("@")
        slash_pos = git_url.rfind("/")
        if at_pos > slash_pos >= 0:
            return git_url[:at_pos], git_url[at_pos + 1 :]

    return git_url, None


def _verify_sha256(path: Path, expected: str | None) -> None:
    """Verify the SHA-256 digest of a downloaded archive."""
    if not expected:
        return

    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def _ensure_member_is_within_root(root: Path, member_name: str) -> Path:
    """Resolve a member path and ensure it stays within the extraction root."""
    resolved_root = root.resolve()
    destination = (root / member_name).resolve()
    if destination == resolved_root or resolved_root in destination.parents:
        return destination
    raise RuntimeError(f"Archive member escapes extraction root: {member_name}")


def _safe_extract_zip(archive_path: Path, source_dir: Path) -> None:
    """Extract a zip archive while rejecting path traversal and symlinks."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            destination = _ensure_member_is_within_root(source_dir, member.filename)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            mode = (member.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RuntimeError(
                    f"Refusing to extract symlink from zip archive: {member.filename}"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _safe_extract_tar(archive_path: Path, source_dir: Path) -> None:
    """Extract a tar archive while rejecting path traversal and links."""
    with tarfile.open(archive_path) as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(
                    f"Refusing to extract link from tar archive: {member.name}"
                )

            destination = _ensure_member_is_within_root(source_dir, member.name)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            extracted = tf.extractfile(member)
            if extracted is None:
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            with extracted, destination.open("wb") as dst:
                shutil.copyfileobj(extracted, dst)


def download_source(
    url: str, dest_dir: Path, name: str, sha256: str | None = None
) -> Path:
    """Download source from a URL.

    Supports:
    - Git repositories (git://, git+https://, .git suffix)
    - HTTP(S) archives (.tar.gz, .tar.bz2, .zip)

    Args:
        url: URL to download from.
        dest_dir: Directory to download/clone to.
        name: Package name (used as subdirectory name).

    Returns:
        Path to the downloaded source.

    Raises:
        RuntimeError: If download fails.
    """
    source_dir = dest_dir / name

    if source_dir.exists():
        logger.info("Source directory already exists: %s", source_dir)
        return source_dir

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Strip @ref suffix before checking for a .git extension
    bare_url = url.split("@")[0] if "@" in url and "://" in url else url
    is_git = (
        url.startswith("git://") or url.startswith("git+") or bare_url.endswith(".git")
    )
    if is_git:
        git_url, ref = _split_git_url_and_ref(url)

        logger.info("Cloning %s", git_url)
        is_sha = (
            ref is not None
            and len(ref) >= 7
            and all(c in "0123456789abcdef" for c in ref)
        )
        if is_sha:
            assert ref is not None  # guarded by is_sha check
            # Commit SHAs require a full clone + checkout (can't use --branch)
            cmd = ["git", "clone", git_url, str(source_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr}")
            result = subprocess.run(
                ["git", "-C", str(source_dir), "checkout", ref],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git checkout {ref} failed: {result.stderr}")
        else:
            cmd = ["git", "clone", "--depth=1"]
            if ref:
                cmd.extend(["--branch", ref])
            cmd.extend([git_url, str(source_dir)])
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr}")

    elif url.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")):
        archive_name = url.split("/")[-1]
        archive_path = dest_dir / archive_name

        logger.info("Downloading %s", url)
        urllib.request.urlretrieve(url, archive_path)
        _verify_sha256(archive_path, sha256)

        logger.info("Extracting %s", archive_path)
        source_dir.mkdir(parents=True, exist_ok=True)

        try:
            if url.endswith(".zip"):
                _safe_extract_zip(archive_path, source_dir)
            else:
                _safe_extract_tar(archive_path, source_dir)
        finally:
            archive_path.unlink(missing_ok=True)

        # If archive contained a single directory, move its contents up
        contents = list(source_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            inner_dir = contents[0]
            for item in inner_dir.iterdir():
                shutil.move(str(item), str(source_dir))
            inner_dir.rmdir()

    else:
        raise RuntimeError(f"Unsupported URL format: {url}")

    return source_dir


def build_cmake(
    source_dir: Path,
    build_dir: Path,
    install_prefix: Path,
    cmake_options: dict[str, str] | None = None,
) -> bool:
    """Build a CMake project.

    Args:
        source_dir: Source directory containing CMakeLists.txt.
        build_dir: Build directory.
        install_prefix: Installation prefix.
        cmake_options: Additional CMake options (-DKEY=VALUE).

    Returns:
        True if build succeeded, False otherwise.
    """
    build_dir.mkdir(parents=True, exist_ok=True)

    cmake = shutil.which("cmake")
    if cmake is None:
        logger.error("CMake not found")
        return False

    # Configure
    logger.info("Configuring CMake project in %s", source_dir)
    cmd = [
        cmake,
        "-S",
        str(source_dir),
        "-B",
        str(build_dir),
        f"-DCMAKE_INSTALL_PREFIX={install_prefix}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]

    if cmake_options:
        for key, value in cmake_options.items():
            cmd.append(f"-D{key}={value}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("CMake configure failed: %s", result.stderr)
        return False

    # Build
    logger.info("Building")
    result = subprocess.run(
        [cmake, "--build", str(build_dir), "--parallel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("CMake build failed: %s", result.stderr)
        return False

    # Install
    logger.info("Installing to %s", install_prefix)
    result = subprocess.run(
        [cmake, "--install", str(build_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("CMake install failed: %s", result.stderr)
        return False

    return True


def build_autotools(
    source_dir: Path,
    build_dir: Path,
    install_prefix: Path,
    configure_options: list[str] | None = None,
) -> bool:
    """Build an autotools project.

    Args:
        source_dir: Source directory containing configure script.
        build_dir: Build directory.
        install_prefix: Installation prefix.
        configure_options: Additional configure options.

    Returns:
        True if build succeeded, False otherwise.
    """
    build_dir.mkdir(parents=True, exist_ok=True)

    configure_script = source_dir / "configure"
    if not configure_script.exists():
        # Try to generate it
        if (source_dir / "autogen.sh").exists():
            logger.info("Running autogen.sh")
            result = subprocess.run(
                ["./autogen.sh"],
                cwd=source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error("autogen.sh failed: %s", result.stderr)
                return False
        elif (source_dir / "configure.ac").exists():
            logger.info("Running autoreconf")
            result = subprocess.run(
                ["autoreconf", "-i"],
                cwd=source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error("autoreconf failed: %s", result.stderr)
                return False

    if not configure_script.exists():
        logger.error("No configure script found in %s", source_dir)
        return False

    # Configure
    logger.info("Configuring autotools project in %s", source_dir)
    cmd = [str(configure_script), f"--prefix={install_prefix}"]
    if configure_options:
        cmd.extend(configure_options)

    result = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Configure failed: %s", result.stderr)
        return False

    # Build
    logger.info("Building")
    make = shutil.which("make")
    if make is None:
        logger.error("make not found")
        return False

    result = subprocess.run(
        [make, "-j"],
        cwd=build_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Make failed: %s", result.stderr)
        return False

    # Install
    logger.info("Installing to %s", install_prefix)
    result = subprocess.run(
        [make, "install"],
        cwd=build_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Make install failed: %s", result.stderr)
        return False

    return True


def _find_pc_files(install_prefix: Path) -> list[Path]:
    """Find .pc (pkg-config) files under an install prefix."""
    pc_files: list[Path] = []
    for pc_dir_name in ["lib/pkgconfig", "lib64/pkgconfig", "share/pkgconfig"]:
        pc_dir = install_prefix / pc_dir_name
        if pc_dir.is_dir():
            pc_files.extend(pc_dir.glob("*.pc"))
    return pc_files


def generate_package_description(
    name: str,
    version: str,
    install_prefix: Path,
    build_system: str,
    system: bool = True,
) -> tuple[PackageDescription, list[Path]]:
    """Generate a PackageDescription for an installed package.

    If the install prefix contains .pc files, returns those instead of
    scanning for libraries (the .pc files are authoritative).  Otherwise
    falls back to scanning include/lib directories.

    Args:
        name: Package name.
        version: Package version.
        install_prefix: Installation prefix.
        build_system: Build system used (for metadata).
        system: Record the include directory as a system include, so the
            fetched headers are held to no warning set of the consumer's.
            On by default: a fetched prefix is third-party by construction,
            and is never a directory the compiler already searches.

    Returns:
        Tuple of (PackageDescription, list of .pc file paths).
        The .pc list is non-empty when pkg-config metadata was found.
    """
    install_prefix = install_prefix.resolve()
    pc_files = _find_pc_files(install_prefix)

    if pc_files:
        # .pc files are authoritative — don't guess from the file tree.
        logger.info(
            "Found pkg-config files: %s",
            ", ".join(p.name for p in pc_files),
        )
        if system:
            logger.info(
                "%s carries pkg-config metadata, which spells its include dirs "
                "-I; pass system=True to find_package() to treat them as "
                "system headers.",
                name,
            )
        return (
            PackageDescription(
                name=name,
                version=version,
                prefix=str(install_prefix),
                found_by=f"pcons-fetch ({build_system})",
            ),
            pc_files,
        )

    # Fallback: scan the install tree for include dirs and libraries.
    include_dirs: list[str] = []
    library_dirs: list[str] = []
    libraries: list[str] = []

    include_dir = install_prefix / "include"
    if include_dir.exists():
        include_dirs.append(str(include_dir))

    for lib_dir_name in ["lib", "lib64"]:
        lib_dir = install_prefix / lib_dir_name
        if lib_dir.exists():
            library_dirs.append(str(lib_dir))

            for lib_file in lib_dir.iterdir():
                # Skip symlinks (e.g. libz.1.dylib -> libz.1.3.1.dylib)
                if lib_file.is_symlink():
                    continue
                if lib_file.suffix in (".a", ".so", ".dylib", ".lib"):
                    lib_name = lib_file.stem
                    if lib_name.startswith("lib"):
                        lib_name = lib_name[3:]
                    # Strip version suffixes (e.g. "z.1.3.1" -> "z")
                    # Versioned names contain dots after the base name
                    base = lib_name.split(".")[0]
                    if base and base not in libraries:
                        libraries.append(base)

    return (
        PackageDescription(
            name=name,
            version=version,
            include_dirs=[] if system else include_dirs,
            system_include_dirs=include_dirs if system else [],
            library_dirs=library_dirs,
            libraries=libraries,
            prefix=str(install_prefix),
            found_by=f"pcons-fetch ({build_system})",
        ),
        [],
    )


def fetch_package(
    name: str,
    pkg_config: dict[str, Any],
    deps_dir: Path,
    output_dir: Path,
) -> bool:
    """Fetch and build a single package.

    Args:
        name: Package name.
        pkg_config: Package configuration from deps.toml.
        deps_dir: Directory for downloaded sources and builds.
        output_dir: Directory to write .pcons-pkg.toml files to.

    Returns:
        True if successful, False otherwise.
    """
    logger.info("Processing package: %s", name)

    url = pkg_config.get("url")
    if not url:
        logger.error("No URL specified for package %s", name)
        return False

    version = pkg_config.get("version", "")
    build_system = pkg_config.get("build", "cmake")
    sha256 = pkg_config.get("sha256")

    source_dir_parent = deps_dir / "src"
    try:
        source_dir = download_source(url, source_dir_parent, name, sha256=sha256)
    except RuntimeError as e:
        logger.error("Failed to download %s: %s", name, e)
        return False

    build_dir = deps_dir / "build" / name
    install_prefix = deps_dir / "install"

    # Build
    success = False
    if build_system == "none":
        # Header-only or pre-built — skip build, use source dir as install prefix
        install_prefix = source_dir
        success = True
    elif build_system == "cmake":
        cmake_options = pkg_config.get("cmake_options", {})
        success = build_cmake(source_dir, build_dir, install_prefix, cmake_options)
    elif build_system == "autotools":
        configure_options = pkg_config.get("configure_options", [])
        success = build_autotools(
            source_dir, build_dir, install_prefix, configure_options
        )
    else:
        logger.error("Unknown build system: %s", build_system)
        return False

    if not success:
        logger.error("Failed to build %s", name)
        return False

    pkg_desc, pc_files = generate_package_description(
        name,
        version,
        install_prefix,
        build_system,
        system=pkg_config.get("system", True),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    pkg_file = output_dir / f"{name}.pcons-pkg.toml"
    pkg_desc.to_toml(pkg_file)

    # If .pc files were found, append a [pkgconfig] section pointing to them.
    # This tells consumers to use pkg-config rather than the (empty) paths/link
    # sections.
    if pc_files:
        import tomli_w

        pc_dirs = sorted({str(p.parent) for p in pc_files})
        pc_section = {"pkgconfig": {"pc_dirs": pc_dirs}}
        with open(pkg_file, "ab") as f:
            f.write(b"\n")
            f.write(tomli_w.dumps(pc_section).encode("utf-8"))

    logger.info("Generated %s", pkg_file)

    return True


# ----- CLI ------------------------------------------------------------------


def _verbosity(f: Callable[..., Any]) -> Callable[..., Any]:
    """The -v/--debug pair every fetch command shares.

    Declared on the group and on each command, so both spellings parse.
    `MergingCommand` is what makes the two agree, and it is also what turns
    them into logging: no command configures logging itself.
    """
    f = click.option("--debug", is_flag=True, help="Enable debug output")(f)
    return click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")(
        f
    )


@click.group(cls=MergingGroup, invoke_without_command=True)
@click.version_option(
    __version__, "--version", prog_name="pcons-fetch", message="%(prog)s %(version)s"
)
@_verbosity
@click.pass_context
def cli(ctx: click.Context, verbose: bool, debug: bool) -> int:
    """Download and build external dependencies for pcons."""
    if ctx.invoked_subcommand is not None:
        return 0
    # No subcommand: fetch deps.toml if there is one, else say what the
    # commands are. ctx.invoke fills the rest from `cmd_fetch`'s own defaults,
    # so they are declared once; it bypasses the command's invoke, so the two
    # shared options are handed over by name.
    if Path("deps.toml").exists():
        return int(ctx.invoke(cmd_fetch, verbose=verbose, debug=debug))
    click.echo(ctx.get_help())
    return 0


@cli.command("fetch", short_help="Fetch and build dependencies")
@click.argument("deps_file", default="deps.toml", required=False)
@click.option(
    "-d", "--deps-dir", default=".deps", help="Dependencies directory (default: .deps)"
)
@click.option(
    "-o",
    "--output-dir",
    default=".",
    help="Output directory for .pcons-pkg.toml files (default: .)",
)
@_verbosity
def cmd_fetch(
    deps_file: str, deps_dir: str, output_dir: str, verbose: bool, debug: bool
) -> int:
    """Fetch and build the packages in DEPS_FILE (default: deps.toml)."""
    deps_path = Path(deps_file)
    if not deps_path.exists():
        logger.error("Deps file not found: %s", deps_path)
        return 1

    try:
        deps_config = load_deps_file(deps_path)
    except Exception as e:
        logger.error("Failed to parse deps file: %s", e)
        return 1

    packages = deps_config.get("packages", {})
    if not packages:
        logger.warning("No packages defined in %s", deps_path)
        return 0

    failed: list[str] = []
    for name, pkg_config in packages.items():
        if not fetch_package(name, pkg_config, Path(deps_dir), Path(output_dir)):
            failed.append(name)

    if failed:
        logger.error("Failed to build packages: %s", ", ".join(failed))
        return 1

    logger.info("Successfully built all packages")
    return 0


@cli.command("list", short_help="List packages in deps.toml")
@click.argument("deps_file", default="deps.toml", required=False)
@_verbosity
def cmd_list(deps_file: str, verbose: bool, debug: bool) -> int:
    """List the packages declared in DEPS_FILE (default: deps.toml)."""
    deps_path = Path(deps_file)
    if not deps_path.exists():
        logger.error("Deps file not found: %s", deps_path)
        return 1

    try:
        deps_config = load_deps_file(deps_path)
    except Exception as e:
        logger.error("Failed to parse deps file: %s", e)
        return 1

    packages = deps_config.get("packages", {})
    if not packages:
        print("No packages defined")
        return 0

    print("Packages:")
    for name, config in packages.items():
        version = config.get("version", "")
        url = config.get("url", "")
        build = config.get("build", "cmake")
        version_str = f" ({version})" if version else ""
        print(f"  {name}{version_str}")
        print(f"    URL: {url}")
        print(f"    Build: {build}")

    return 0


@cli.command("clean", short_help="Clean fetched sources and builds")
@click.option(
    "-d", "--deps-dir", default=".deps", help="Dependencies directory (default: .deps)"
)
@click.option("-a", "--all", is_flag=True, help="Remove everything including sources")
@_verbosity
def cmd_clean(deps_dir: str, all: bool, verbose: bool, debug: bool) -> int:
    """Remove the build directory, or with --all the whole deps directory."""
    deps_path = Path(deps_dir)

    if not deps_path.exists():
        logger.info("Dependencies directory does not exist: %s", deps_path)
        return 0

    if all:
        logger.info("Removing entire dependencies directory: %s", deps_path)
        shutil.rmtree(deps_path)
    else:
        # Just remove build directory
        build_dir = deps_path / "build"
        if build_dir.exists():
            logger.info("Removing build directory: %s", build_dir)
            shutil.rmtree(build_dir)

    logger.info("Clean complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point for pcons-fetch."""
    try:
        result = cli.main(args=argv, prog_name="pcons-fetch", standalone_mode=False)
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Exit as e:
        return e.exit_code
    except click.exceptions.Abort:
        return 130
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
