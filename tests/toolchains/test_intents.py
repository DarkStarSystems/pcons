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
from pcons.toolchains.presets import CrossPreset, linux_cross
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
    env.apply_cross_preset(
        CrossPreset(
            name="linux-aarch64",
            arch="aarch64",
            triple="aarch64-linux-gnu",
            tool_cmds={"fc": "aarch64-linux-gnu-gfortran"},
        )
    )
    with pytest.raises(ValueError, match="GCC selects targets by binary"):
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


# =============================================================================
# A feature applies fully or raises
# =============================================================================


def _llvm_flang(tmp_path) -> Environment:
    env = _env(tmp_path, "llvm")
    _add(env, "flang")
    return env


def test_a_feature_one_compiler_lacks_raises(tmp_path) -> None:
    """flang has no sanitizer: sanitizing only the C and C++ sources isn't
    a sanitized build, so it's an error, and nothing is applied."""
    env = _llvm_flang(tmp_path)
    with pytest.raises(ValueError, match="'sanitize' isn't available with flang"):
        env.apply_preset("sanitize")
    assert not any("sanitize" in str(f) for f in env.cc.flags)
    assert not env.has_preset("sanitize")


def test_allow_partial_leaves_out_that_compiler(tmp_path) -> None:
    env = _llvm_flang(tmp_path)
    env.apply_preset("sanitize", allow_partial=True)
    assert any("-fsanitize" in str(f) for f in env.cc.flags)
    assert not any("sanitize" in str(f) for f in env.fc.flags)
    assert "sanitize - flang" in str(env.explain())


def test_has_preset_is_whether_apply_preset_would_raise(tmp_path) -> None:
    alone = _env(tmp_path / "alone", "llvm")
    assert alone.has_preset("sanitize")
    mixed = _llvm_flang(tmp_path / "mixed")
    assert not mixed.has_preset("sanitize")
    assert mixed.has_preset("werror")


def test_a_toolchain_added_later_must_realize_it_too(tmp_path) -> None:
    env = _env(tmp_path, "llvm")
    env.apply_preset("sanitize")
    with pytest.raises(ValueError, match="'sanitize' isn't available with flang"):
        _add(env, "flang")


def test_allow_partial_holds_for_a_toolchain_added_later(tmp_path) -> None:
    env = _env(tmp_path, "llvm")
    env.apply_preset("sanitize", allow_partial=True)
    _add(env, "flang")
    assert "sanitize - flang" in str(env.explain())


def test_a_contributed_preset_decides_for_itself(tmp_path) -> None:
    """A registry resolver returning None for a toolchain is its decision,
    not a gap."""
    from pcons.core import preset as preset_mod
    from pcons.core.preset import ToolContribution, register_preset

    def resolve(toolchain):
        if toolchain.name != "llvm":
            return None
        return [ToolContribution("cc", flags=("-DCLANG_ONLY",))]

    register_preset("test/clang-only", resolve)
    try:
        env = _llvm_flang(tmp_path)
        env.apply_preset("test/clang-only")
        assert "-DCLANG_ONLY" in env.cc.flags
    finally:
        preset_mod._PRESET_REGISTRY.pop("test/clang-only")


def test_an_arch_one_compiler_cant_build_for_raises(tmp_path, monkeypatch) -> None:
    """swiftc can't take set_target_arch's arch; its sources would be
    built for the host."""
    mac = platform_mod.Platform(
        os="darwin",
        arch="arm64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".dylib",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )
    monkeypatch.setattr(platform_mod, "_platform", mac)
    env = _env(tmp_path, "swiftc")
    _add(env, "llvm")
    with pytest.raises(ValueError, match="swift can't build for target arch"):
        env.set_target_arch("x86_64")


# =============================================================================
# From the review of the redesign
# =============================================================================


def test_a_failed_add_toolchain_changes_nothing(tmp_path) -> None:
    """flang can't take sanitize: adding it raises before it registers or
    configures anything, so a script can try another compiler."""
    env = _env(tmp_path, "llvm")
    env.apply_preset("sanitize")
    tools = set(env.tool_names())
    with pytest.raises(ValueError, match="'sanitize' isn't available with flang"):
        _add(env, "flang")
    assert [t.name for t in env.toolchains] == ["llvm"]
    assert set(env.tool_names()) == tools
    _add(env, "gfortran")  # which can
    assert any("-fsanitize" in str(f) for f in env.fc.flags)


def test_a_refused_cross_target_changes_nothing(tmp_path) -> None:
    env = _env(tmp_path, "llvm")
    env.apply_cross_preset(linux_cross("aarch64-linux-gnu"))
    with pytest.raises(ValueError, match="selects targets by binary"):
        _add(env, "gfortran")
    assert not env.has_tool("fc")


def test_set_target_arch_changes_nothing_when_it_raises(tmp_path, monkeypatch) -> None:
    mac = platform_mod.Platform(
        os="darwin",
        arch="arm64",
        is_64bit=True,
        exe_suffix="",
        shared_lib_suffix=".dylib",
        shared_lib_prefix="lib",
        static_lib_suffix=".a",
        static_lib_prefix="lib",
        object_suffix=".o",
    )
    monkeypatch.setattr(platform_mod, "_platform", mac)
    env = _env(tmp_path, "llvm")
    _add(env, "gfortran")
    with pytest.raises(ValueError, match="cannot retarget"):
        env.set_target_arch("x86_64")
    assert "-arch" not in env.cc.flags
    assert env.get("target_arch") is None


def test_cuda_builds_for_the_host_arch(tmp_path) -> None:
    """nvcc builds for the host CPU, so the host's arch is a no-op and
    another one an error."""
    env = _env(tmp_path, "gcc")
    _add(env, "cuda")
    with pytest.raises(ValueError, match="cannot retarget|can't build"):
        env.set_target_arch("arm64")
    from pcons.toolchains.cuda import CudaToolchain

    assert CudaToolchain().make_arch_preset("x64") is not None  # host: x86_64


def test_a_preset_whose_flags_all_land_on_others_tools_raises(tmp_path) -> None:
    from pcons.core import preset as preset_mod
    from pcons.core.preset import ToolContribution, register_preset

    def resolve(toolchain):
        if toolchain.name != "gfortran":
            return None
        return [ToolContribution("link", flags=("-static-libgfortran",))]

    register_preset("test/static-fortran", resolve)
    try:
        env = _env(tmp_path, "llvm")
        _add(env, "gfortran")
        with pytest.raises(ValueError, match="would have no effect"):
            env.apply_preset("test/static-fortran")
    finally:
        preset_mod._PRESET_REGISTRY.pop("test/static-fortran")


def test_add_toolchain_keeps_a_group_preset_applied_directly(tmp_path) -> None:
    from pcons.core.preset import VARIANT_GROUP, Preset, ToolContribution

    env = _env(tmp_path, "gcc")
    env.set_variant("release")  # replayed for gfortran when it's added
    env.apply(
        Preset(
            name="fastbuild",
            category="variant",
            exclusive_group=VARIANT_GROUP,
            contributions=(ToolContribution("cxx", flags=("-O1",)),),
        )
    )
    _add(env, "gfortran")
    assert "-O1" in env.cxx.flags
    assert env.variant == "fastbuild"
