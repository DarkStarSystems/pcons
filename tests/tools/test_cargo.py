# SPDX-License-Identifier: MIT
"""Unit tests for the CargoBuild integration (pcons/tools/cargo.py).

These cover the pure helpers and the build-graph construction in
create_target without invoking a real cargo/cbindgen (the example tests
in tests/test_examples.py exercise the end-to-end build where cargo is
available).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pcons import Project
from pcons.tools.cargo import (
    _artifact_filename,
    _profile_subdir,
    _read_crate_name,
)

CARGO_TOML = """\
[package]
name = "{name}"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["staticlib"]
"""


def _write_crate(root, *, package="rust_core", lib_name=None):
    """Create a minimal crate under <root>/rust and return its dir."""
    crate = root / "rust"
    (crate / "src").mkdir(parents=True)
    toml = CARGO_TOML.format(name=package)
    if lib_name is not None:
        toml += f'name = "{lib_name}"\n'
    (crate / "Cargo.toml").write_text(toml)
    (crate / "Cargo.lock").write_text("")
    (crate / "src" / "lib.rs").write_text("// empty\n")
    return crate


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_read_crate_name_uses_lib_name_when_set(tmp_path):
    crate = _write_crate(tmp_path, package="my-pkg", lib_name="custom_lib")
    assert _read_crate_name(crate / "Cargo.toml") == "custom_lib"


def test_read_crate_name_falls_back_to_package_name_with_underscores(tmp_path):
    crate = _write_crate(tmp_path, package="my-cool-pkg")
    # Hyphens become underscores (cargo's own library-name rule).
    assert _read_crate_name(crate / "Cargo.toml") == "my_cool_pkg"


def test_read_crate_name_missing_package_name_raises(tmp_path):
    bad = tmp_path / "Cargo.toml"
    bad.write_text("[dependencies]\n")
    with pytest.raises(ValueError, match="no \\[package\\] name"):
        _read_crate_name(bad)


@pytest.mark.parametrize(
    "crate_type,expected",
    [
        ("staticlib", "foo.lib" if sys.platform == "win32" else "libfoo.a"),
        (
            "cdylib",
            "foo.dll"
            if sys.platform == "win32"
            else ("libfoo.dylib" if sys.platform == "darwin" else "libfoo.so"),
        ),
        ("bin", "foo.exe" if sys.platform == "win32" else "foo"),
    ],
)
def test_artifact_filename(crate_type, expected):
    assert _artifact_filename("foo", crate_type) == expected


def test_artifact_filename_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unsupported crate_type"):
        _artifact_filename("foo", "rlib")


@pytest.mark.parametrize(
    "triple,crate_type,expected",
    [
        # Cross-compiling: cargo names artifacts by the TARGET's
        # convention, regardless of host.
        ("x86_64-pc-windows-msvc", "staticlib", "foo.lib"),
        ("x86_64-pc-windows-gnu", "staticlib", "libfoo.a"),
        ("x86_64-unknown-linux-gnu", "staticlib", "libfoo.a"),
        ("aarch64-apple-darwin", "staticlib", "libfoo.a"),
        ("x86_64-pc-windows-msvc", "cdylib", "foo.dll"),
        ("aarch64-apple-darwin", "cdylib", "libfoo.dylib"),
        ("x86_64-unknown-linux-musl", "cdylib", "libfoo.so"),
        ("wasm32-wasi", "cdylib", "foo.wasm"),
        ("wasm32-wasi", "bin", "foo.wasm"),
        ("x86_64-pc-windows-msvc", "bin", "foo.exe"),
        ("x86_64-unknown-linux-gnu", "bin", "foo"),
    ],
)
def test_artifact_filename_cross_compile(triple, crate_type, expected):
    assert _artifact_filename("foo", crate_type, triple) == expected


@pytest.mark.parametrize(
    "profile,expected",
    [("dev", "debug"), ("release", "release"), ("bench", "bench")],
)
def test_profile_subdir(profile, expected):
    assert _profile_subdir(profile) == expected


# ---------------------------------------------------------------------------
# create_target (build-graph construction; no real cargo run)
# ---------------------------------------------------------------------------


@pytest.fixture
def project_env(tmp_path, gcc_toolchain):
    project = Project("cargo_test", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    return project, env


def test_cargo_build_rejects_bad_crate_type(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path)
    with pytest.raises(ValueError, match="crate_type"):
        project.CargoBuild(
            "rust_core", env, manifest="rust/Cargo.toml", crate_type="rlib"
        )


def test_cargo_build_missing_manifest_raises(project_env):
    project, env = project_env
    with pytest.raises(FileNotFoundError):
        project.CargoBuild("rust_core", env, manifest="rust/Cargo.toml")


def test_cargo_build_wraps_imported_target(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_core")

    target = project.CargoBuild("rust_core", env, manifest="rust/Cargo.toml")

    assert target.name == "rust_core"
    assert target.is_imported
    # The crate name is exposed as a link library, with its artifact dir.
    assert "rust_core" in target.public.link_libs
    assert target.public.link_dirs
    # Depends on the underlying cargo command target so Ninja relinks
    # consumers when the artifact changes.
    dep_names = {d.name for d in target.dependencies}
    staticlib = _artifact_filename("rust_core", "staticlib")
    assert f"cargo/rust_core/release/{staticlib}" in dep_names
    # No header generation requested -> no include dirs, no cbindgen dep.
    assert not target.public.include_dirs
    assert not any(name.endswith(".h") for name in dep_names)


def test_cargo_build_command_includes_options(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_core")

    # Exercises the optional cargo-command paths (custom profile, features,
    # target triple, extra args). A target triple nests the artifact under
    # target/<triple>/<profile>/, which surfaces in the link dirs.
    target = project.CargoBuild(
        "rust_core",
        env,
        manifest="rust/Cargo.toml",
        profile="custom",
        features=["a", "b"],
        target_triple="wasm32-wasi",
        extra_args=["--locked"],
    )

    assert any("wasm32-wasi" in str(p) for p in target.public.link_dirs)
    assert any("custom" in str(p) for p in target.public.link_dirs)


def test_cargo_build_bin_returns_command_target(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_tool")

    target = project.CargoBuild(
        "rust_tool", env, manifest="rust/Cargo.toml", crate_type="bin"
    )

    # A bin crate has nothing to link: the cargo Command target itself is
    # returned, with the executable as its output — no library usage
    # requirements and no ImportedTarget wrapper.
    assert (
        target.name
        == f"cargo/rust_tool/release/{_artifact_filename('rust_tool', 'bin')}"
    )
    assert not getattr(target, "is_imported", False)
    assert not target.public.link_libs


def test_cargo_build_bin_rejects_generate_header(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_tool")
    (tmp_path / "rust" / "cbindgen.toml").write_text('language = "C"\n')

    with pytest.raises(ValueError, match="generate_header"):
        project.CargoBuild(
            "rust_tool",
            env,
            manifest="rust/Cargo.toml",
            crate_type="bin",
            generate_header="rust/cbindgen.toml",
        )


def test_cargo_build_cdylib_windows_uses_import_lib_name(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_core")

    target = project.CargoBuild(
        "rust_core",
        env,
        manifest="rust/Cargo.toml",
        crate_type="cdylib",
        target_triple="x86_64-pc-windows-msvc",
    )

    # rustc's MSVC-target cdylib import library is <crate>.dll.lib; naming
    # the link lib "<crate>.dll" lets MSVC (appends .lib) and MinGW
    # (searches lib<name>.a) both resolve it.
    assert "rust_core.dll" in target.public.link_libs


def test_cargo_build_with_cbindgen_adds_header_and_dep(project_env, tmp_path):
    project, env = project_env
    _write_crate(tmp_path, package="rust_core")
    (tmp_path / "rust" / "cbindgen.toml").write_text('language = "C"\n')

    target = project.CargoBuild(
        "rust_core",
        env,
        manifest="rust/Cargo.toml",
        generate_header="rust/cbindgen.toml",
    )

    # Header generation adds an include dir and a cbindgen command dep.
    assert target.public.include_dirs
    dep_names = {d.name for d in target.dependencies}
    staticlib = _artifact_filename("rust_core", "staticlib")
    assert f"cargo/rust_core/release/{staticlib}" in dep_names
    assert "cargo/rust_core/include/rust_core.h" in dep_names

    # cbindgen reports the sources it read in a depfile of its own.
    project.resolve()
    header = _cargo_edges(target)["rust_core.h"]
    assert header._build_info["depfile"].suffix == ".d"
    assert "--depfile" in header._build_info["command"]


def _cargo_edges(target):
    """The primary output node of each Command a CargoBuild made, by file name."""
    return {
        dep.output_nodes[0].path.name: dep.output_nodes[0]
        for dep in target.dependencies
    }


def test_cargo_build_takes_its_sources_from_cargos_dep_info(project_env, tmp_path):
    """#173: the crate's sources come from the dep-info file cargo writes
    as it builds, not a glob made while describing the build, so a module
    added afterwards is tracked. Only the manifest and lock file, which that
    file leaves out, are declared."""
    project, env = project_env
    crate = _write_crate(tmp_path, package="rust_core")
    (crate / "src" / "extra.rs").write_text("// more\n")
    target = project.CargoBuild("rust_core", env, manifest="rust/Cargo.toml")
    project.resolve()

    staticlib = Path(_artifact_filename("rust_core", "staticlib"))
    edge = _cargo_edges(target)[staticlib.name]
    depfile = edge._build_info["depfile"]
    assert Path(depfile.path) == Path(
        "build/cargo/rust_core/release", staticlib.with_suffix(".d")
    )
    assert not depfile.suffix
    # The depfile never lists these, so they rerun the edge themselves
    # rather than only being built first.
    assert {d.path.name for d in edge.implicit_deps} == {"Cargo.toml", "Cargo.lock"}


@pytest.mark.parametrize(
    "preamble",
    [
        "from pcons.core.project import Project\n"
        "project = Project('child')\n"
        "env = project.parent.default_environment\n",
        "from pcons import context\n"
        "project = context.current_project\n"
        "env = project.default_environment\n",
    ],
    ids=["own-project", "parent-project"],
)
def test_cargo_build_under_add_subdirectory(tmp_path, monkeypatch, preamble):
    """Declared in a subdirectory, the crate is read from that directory and
    cargo writes where the artifact node says, build/<subdir>/cargo/."""
    from pcons.util.add_subdirectory import add_subdirectory

    monkeypatch.chdir(tmp_path)
    top = Project("top", root_dir=tmp_path, build_dir="build")
    top.Environment()
    child = tmp_path / "child"
    _write_crate(child, package="rust_core")
    (child / "pcons-build.py").write_text(
        preamble
        + "lib = project.CargoBuild('rust_core', env, manifest='rust/Cargo.toml')\n"
    )

    lib = add_subdirectory("child").lib
    top.resolve()

    staticlib = _artifact_filename("rust_core", "staticlib")
    edge = _cargo_edges(lib)[staticlib]
    target_dir = Path("build/child/cargo/rust_core")
    assert edge.path == target_dir / "release" / staticlib
    command = edge._build_info["command"]
    assert f"--target-dir={tmp_path.resolve() / target_dir}" in command
    assert f"--manifest-path={child / 'rust' / 'Cargo.toml'}" in command
    # Cargo names the artifact in its dep-info as the build tool does.
    basedir = json.dumps(str(tmp_path.resolve() / "build"))
    assert f"--config=build.dep-info-basedir={basedir}" in command
