# SPDX-License-Identifier: MIT
"""Unit tests for the CargoBuild integration (pcons/tools/cargo.py).

These cover the pure helpers and the build-graph construction in
create_target without invoking a real cargo/cbindgen (the example tests
in tests/test_examples.py exercise the end-to-end build where cargo is
available).
"""

from __future__ import annotations

import sys

import pytest

from pcons import Project
from pcons.tools.cargo import (
    _artifact_filename,
    _collect_rust_sources,
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
    "profile,expected",
    [("dev", "debug"), ("release", "release"), ("bench", "bench")],
)
def test_profile_subdir(profile, expected):
    assert _profile_subdir(profile) == expected


def test_collect_rust_sources_globs_and_skips_target(tmp_path):
    crate = _write_crate(tmp_path)
    (crate / "src" / "extra.rs").write_text("// more\n")
    # Anything under target/ is cargo's own output and must be ignored.
    (crate / "target").mkdir()
    (crate / "target" / "stale.rs").write_text("// junk\n")

    names = {p.name for p in _collect_rust_sources(crate)}
    assert {"lib.rs", "extra.rs", "Cargo.toml", "Cargo.lock"} <= names
    assert not any("target" in p.parts for p in _collect_rust_sources(crate))


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
    assert "rust_core_cargo" in dep_names
    # No header generation requested -> no include dirs, no cbindgen dep.
    assert not target.public.include_dirs
    assert "rust_core_cbindgen" not in dep_names


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
    assert "rust_core_cargo" in dep_names
    assert "rust_core_cbindgen" in dep_names
