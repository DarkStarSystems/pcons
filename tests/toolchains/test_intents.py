# SPDX-License-Identifier: MIT
"""Knobs and features are recorded as intents, so a toolchain added later
realizes them too, as if it had been there from the start."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from pcons import Project
from pcons.configure import platform as platform_mod
from pcons.core.environment import Environment
from pcons.toolchains.presets import linux_cross
from pcons.tools.toolchain import toolchain_registry


@pytest.fixture(autouse=True)
def on_linux(monkeypatch):
    from pcons.toolchains import fortran as fortran_mod
    from pcons.toolchains import unix as unix_mod

    linux = platform_mod.Platform(
        os="linux",
        arch="x86_64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".so",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )
    monkeypatch.setattr(platform_mod, "_platform", linux)
    monkeypatch.setattr(unix_mod, "target_triple", lambda compiler: None)
    monkeypatch.setattr(
        fortran_mod.FortranToolchainBase,
        "runtime_libdir",
        property(lambda self: None),
    )


def _env(tmp_path, primary: str) -> Environment:
    project = Project("intents", root_dir=tmp_path, build_dir="build")
    entry = toolchain_registry.get(primary)
    assert entry is not None
    return project.Environment(toolchain=entry.create_toolchain())


def _add(env: Environment, name: str) -> None:
    entry = toolchain_registry.get(name)
    assert entry is not None
    env.add_toolchain(entry.create_toolchain())


def _flags(env: Environment) -> dict[str, Counter[str]]:
    """Each tool's flags and defines, ignoring order."""
    result = {}
    for name in env.tool_names():
        tool = getattr(env, name)
        values = [*(getattr(tool, "flags", None) or ())]
        values += [f"-D{d}" for d in getattr(tool, "defines", None) or ()]
        result[name] = Counter(str(v) for v in values)
    return result


def _several(env: Environment) -> None:
    env.set_variant("release")
    env.apply_preset("warnings")
    env.apply_preset("werror")


KNOBS: dict[str, Callable[[Environment], None]] = {
    "variant": lambda env: env.set_variant("debug"),
    "feature": lambda env: env.apply_preset("warnings"),
    "several": _several,
}


@pytest.mark.parametrize("knob", KNOBS)
@pytest.mark.parametrize(
    "primary, added", [("gcc", "gfortran"), ("llvm", "flang"), ("flang", "llvm")]
)
def test_order_doesnt_matter(tmp_path, primary, added, knob) -> None:
    """Applying knobs before add_toolchain() gives the same tools as after."""
    apply = KNOBS[knob]
    before = _env(tmp_path / "before", primary)
    apply(before)
    _add(before, added)

    after = _env(tmp_path / "after", primary)
    _add(after, added)
    apply(after)

    assert _flags(before) == _flags(after)


def test_the_cxx_standard_reaches_a_toolchain_added_later(tmp_path) -> None:
    env = _env(tmp_path, "gcc")
    env.cxx.set_standard("c++20")
    _add(env, "llvm")  # its cxx isn't new, so nothing changes
    assert list(env.cxx.flags).count("-std=c++20") == 1


def test_a_feature_reaches_a_toolchain_added_later(tmp_path) -> None:
    env = _env(tmp_path, "gcc")
    env.apply_preset("warnings")
    _add(env, "gfortran")
    assert "-Wall" in env.fc.flags


def test_a_cross_target_reaches_a_toolchain_added_later(tmp_path) -> None:
    env = _env(tmp_path, "flang")
    env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))
    _add(env, "llvm")
    assert "--target=aarch64-linux-gnu" in env.cc.flags


def test_a_toolchain_that_cant_take_the_cross_target_fails(tmp_path) -> None:
    """gcc can't retarget by flag; added to a cross environment, it says
    so rather than quietly compiling for the host."""
    env = _env(tmp_path, "gfortran")
    env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))
    with pytest.raises(ValueError, match="selects targets by binary"):
        _add(env, "gcc")


def test_only_the_last_variant_is_replayed(tmp_path) -> None:
    env = _env(tmp_path, "gcc")
    env.set_variant("debug")
    env.set_variant("release")
    _add(env, "gfortran")
    assert "-O2" in env.fc.flags and "-g" not in env.fc.flags
    assert env.variant == "release"


def test_a_failed_knob_isnt_recorded(tmp_path) -> None:
    env = _env(tmp_path, "gcc")
    with pytest.raises(ValueError, match="Unknown variant"):
        env.set_variant("nonsense")
    with pytest.raises(ValueError, match="Unknown preset"):
        env.apply_preset("nonsense")
    _add(env, "gfortran")  # nothing to replay, so nothing raises
    assert "-O2" not in env.fc.flags


def test_a_clone_keeps_the_intents(tmp_path) -> None:
    env = _env(tmp_path, "gcc")
    env.apply_preset("warnings")
    clone = env.clone()
    _add(clone, "gfortran")
    assert "-Wall" in clone.fc.flags
