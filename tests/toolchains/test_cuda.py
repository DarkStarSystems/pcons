# SPDX-License-Identifier: MIT
"""Tests for CUDA toolchain."""

import sys

import pytest

from pcons.configure.platform import Platform, get_platform
from pcons.toolchains import CudaCompiler, CudaToolchain, find_cuda_toolchain
from pcons.tools.toolchain import SourceHandler

_WINDOWS_PLATFORM = Platform(
    os="windows",
    arch="x86_64",
    is_64bit=True,
    exe_suffix=".exe",
    shared_lib_suffix=".dll",
    shared_lib_prefix="",
    static_lib_suffix=".lib",
    static_lib_prefix="",
    object_suffix=".obj",
)

_LINUX_PLATFORM = Platform(
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


class TestCudaCompiler:
    """Tests for CudaCompiler tool."""

    def test_name(self):
        """CudaCompiler has correct name."""
        compiler = CudaCompiler()
        assert compiler.name == "cuda"

    def test_language(self):
        """CudaCompiler identifies as CUDA language."""
        compiler = CudaCompiler()
        assert compiler.language == "cuda"

    def test_default_vars(self):
        """CudaCompiler provides expected default variables."""
        compiler = CudaCompiler()
        defaults = compiler.default_vars()

        assert defaults["cmd"] == "nvcc"
        assert defaults["arch"] == "sm_75"
        assert isinstance(defaults["flags"], list)
        assert isinstance(defaults["includes"], list)
        assert isinstance(defaults["defines"], list)
        assert "objcmd" in defaults

    def test_builders(self):
        """CudaCompiler provides Object builder."""
        compiler = CudaCompiler()
        builders = compiler.builders()

        assert "Object" in builders
        builder = builders["Object"]
        assert ".cu" in builder.src_suffixes
        assert builder.language == "cuda"


class TestCudaToolchain:
    """Tests for CudaToolchain."""

    def test_name(self):
        """CudaToolchain has correct name."""
        toolchain = CudaToolchain()
        assert toolchain.name == "cuda"

    def test_source_handler_cu(self):
        """CudaToolchain handles .cu files."""
        toolchain = CudaToolchain()
        handler = toolchain.get_source_handler(".cu")

        assert handler is not None
        assert isinstance(handler, SourceHandler)
        assert handler.tool_name == "cuda"
        assert handler.language == "cuda"
        assert handler.object_suffix == get_platform().object_suffix

    def test_source_handler_unknown(self):
        """CudaToolchain returns None for unknown suffixes."""
        toolchain = CudaToolchain()
        assert toolchain.get_source_handler(".c") is None
        assert toolchain.get_source_handler(".cpp") is None
        assert toolchain.get_source_handler(".txt") is None

    def test_object_suffix(self):
        """CudaToolchain matches the host platform's object suffix."""
        toolchain = CudaToolchain()
        assert toolchain.get_object_suffix() == get_platform().object_suffix

    def test_object_suffix_windows(self, monkeypatch):
        """On Windows, nvcc objects pair with MSVC's .obj convention."""
        monkeypatch.setattr(
            "pcons.toolchains.cuda.get_platform", lambda: _WINDOWS_PLATFORM
        )
        toolchain = CudaToolchain()
        assert toolchain.get_object_suffix() == ".obj"
        handler = toolchain.get_source_handler(".cu")
        assert handler is not None
        assert handler.object_suffix == ".obj"

    def test_object_suffix_linux(self, monkeypatch):
        """On Linux, nvcc objects pair with GCC/Clang's .o convention."""
        monkeypatch.setattr(
            "pcons.toolchains.cuda.get_platform", lambda: _LINUX_PLATFORM
        )
        toolchain = CudaToolchain()
        assert toolchain.get_object_suffix() == ".o"
        handler = toolchain.get_source_handler(".cu")
        assert handler is not None
        assert handler.object_suffix == ".o"


class TestCudaVariants:
    """Tests for CUDA variant support."""

    def test_apply_debug_variant(self, test_project):  # noqa: F811
        """Debug variant adds debug flags."""
        from pcons.core.environment import Environment

        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True

        env = Environment()
        toolchain.setup(env)

        # Initially no debug flags
        assert "-G" not in env.cuda.flags
        assert "-g" not in env.cuda.flags

        # Apply debug variant
        toolchain.apply_variant(env, "debug")

        # Should have debug flags
        assert "-G" in env.cuda.flags
        assert "-g" in env.cuda.flags
        assert "DEBUG" in env.cuda.defines

    def test_apply_release_variant(self, test_project):  # noqa: F811
        """Release variant adds optimization flags."""
        from pcons.core.environment import Environment

        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True

        env = Environment()
        toolchain.setup(env)

        # Apply release variant
        toolchain.apply_variant(env, "release")

        # Should have optimization
        assert "-O3" in env.cuda.flags
        assert "NDEBUG" in env.cuda.defines
        # Should NOT have debug flags
        assert "-G" not in env.cuda.flags

    def test_apply_profile_variant(self, test_project):  # noqa: F811
        """Profile variant adds line info for profilers."""
        from pcons.core.environment import Environment

        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True

        env = Environment()
        toolchain.setup(env)

        # Apply profile variant
        toolchain.apply_variant(env, "profile")

        # Should have line info
        assert "-lineinfo" in env.cuda.flags
        assert "-O3" in env.cuda.flags


class TestFindCudaToolchain:
    """Tests for find_cuda_toolchain()."""

    def test_returns_none_when_nvcc_not_available(self, monkeypatch):
        """Returns None when nvcc is not in PATH."""
        import shutil

        # Mock shutil.which to return None
        monkeypatch.setattr(shutil, "which", lambda x: None)

        result = find_cuda_toolchain()
        assert result is None

    def test_returns_toolchain_when_nvcc_available(self, monkeypatch):
        """Returns CudaToolchain when nvcc is available."""
        import shutil

        # Mock shutil.which to return a path for nvcc
        def mock_which(cmd):
            if cmd == "nvcc":
                return "/usr/local/cuda/bin/nvcc"
            return None

        monkeypatch.setattr(shutil, "which", mock_which)

        result = find_cuda_toolchain()
        assert result is not None
        assert isinstance(result, CudaToolchain)
        assert "cuda" in result.tools


def _on(monkeypatch, platform: Platform) -> None:
    monkeypatch.setattr("pcons.toolchains.cuda.get_platform", lambda: platform)


class TestCudaFeatures:
    """nvcc's own realization of each feature, compile side only."""

    def test_features_realize_on_nvcc(self, monkeypatch):
        _on(monkeypatch, _LINUX_PLATFORM)
        toolchain = CudaToolchain()
        assert toolchain._feature_preset_tools() == ("cuda",)
        expected = {
            "warnings": ("-Xcompiler=-Wall", "-Xcompiler=-Wextra"),
            "werror": ("-Werror=all-warnings",),
            "sanitize": (
                "-Xcompiler=-fsanitize=address",
                "-Xcompiler=-fsanitize=undefined",
                "-Xcompiler=-fno-omit-frame-pointer",
            ),
            "profile": ("-pg",),
            "hardened": (
                "-Xcompiler=-fstack-protector-strong",
                "-D_FORTIFY_SOURCE=2",
                "-Xcompiler=-fPIE",
            ),
            "pthread": ("-Xcompiler=-pthread",),
            "coverage": ("-Xcompiler=--coverage",),
            "fast-math": ("-use_fast_math", "-Xcompiler=-ffast-math"),
            "openmp": ("-Xcompiler=-fopenmp",),
        }
        for name, flags in expected.items():
            preset = toolchain.make_feature_preset(name)
            assert preset is not None, name
            assert [(c.tool, c.flags) for c in preset.contributions] == [
                ("cuda", flags)
            ], name

    def test_no_lto(self, monkeypatch):
        _on(monkeypatch, _LINUX_PLATFORM)
        assert CudaToolchain().make_feature_preset("lto") is None

    @pytest.mark.parametrize(
        "name, flags",
        [
            ("warnings", ("-Xcompiler=/W4",)),
            ("werror", ("-Werror=all-warnings",)),
            ("sanitize", ("-Xcompiler=/fsanitize=address",)),
            ("hardened", ("-Xcompiler=/GS", "-Xcompiler=/guard:cf")),
            ("fast-math", ("-use_fast_math", "-Xcompiler=/fp:fast")),
            ("openmp", ("-Xcompiler=/openmp",)),
        ],
    )
    def test_msvc_host_flags_on_windows(self, monkeypatch, name, flags):
        """On Windows the host compiler is cl.exe, so the host flags are
        MSVC's (checked with nvcc 12.5 and cl 19.44)."""
        _on(monkeypatch, _WINDOWS_PLATFORM)
        preset = CudaToolchain().make_feature_preset(name)
        assert preset is not None
        assert [(c.tool, c.flags) for c in preset.contributions] == [("cuda", flags)]

    def test_on_windows_profile_is_the_linkers(self, monkeypatch):
        _on(monkeypatch, _WINDOWS_PLATFORM)
        preset = CudaToolchain().make_feature_preset("profile")
        assert preset is not None and preset.contributions == ()

    @pytest.mark.parametrize("name", ["lto", "pthread", "coverage"])
    def test_unavailable_on_windows_as_with_msvc(self, monkeypatch, name):
        _on(monkeypatch, _WINDOWS_PLATFORM)
        assert CudaToolchain().make_feature_preset(name) is None

    def test_shared_libraries_are_pic_through_xcompiler(self, monkeypatch):
        _on(monkeypatch, _LINUX_PLATFORM)
        toolchain = CudaToolchain()
        assert toolchain.get_compile_flags_for_target_type("shared_library") == [
            "-Xcompiler=-fPIC"
        ]
        assert toolchain.get_compile_flags_for_target_type("program") == []

    def test_no_pic_on_windows(self, monkeypatch):
        _on(monkeypatch, _WINDOWS_PLATFORM)
        flags = CudaToolchain().get_compile_flags_for_target_type("shared_library")
        assert flags == []


class TestCudaCrossTargets:
    def _env(self):
        from pcons.core.environment import Environment

        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True
        env = Environment()
        toolchain.setup(env)
        return toolchain, env

    def test_a_triple_is_refused(self, test_project):
        from pcons.toolchains.presets import linux_cross

        toolchain, env = self._env()
        with pytest.raises(ValueError, match="CUDA cross targets aren't supported"):
            toolchain.apply_cross_preset(env, linux_cross("aarch64-linux-gnu"))

    def test_a_preset_without_a_triple_applies(self, test_project):
        from pcons.toolchains.presets import CrossPreset

        toolchain, env = self._env()
        preset = CrossPreset(
            name="pinned-nvcc", arch="x86_64", tool_cmds={"cuda": "/opt/cuda/nvcc"}
        )
        toolchain.apply_cross_preset(env, preset)
        assert env.cuda.cmd == "/opt/cuda/nvcc"


class TestCudaRuntime:
    """env.cuda.runtime: the CUDA runtime a link with CUDA objects needs."""

    @pytest.fixture(autouse=True)
    def _project(self, test_project):  # noqa: F811
        return test_project

    @staticmethod
    def _toolchain(monkeypatch, platform: Platform) -> CudaToolchain:
        """A CUDA toolchain on *platform*, as host and as target."""
        from pcons.configure import platform as platform_mod

        monkeypatch.setattr(platform_mod, "_platform", platform)
        _on(monkeypatch, platform)
        toolchain = CudaToolchain()
        toolchain._tools = {"cuda": CudaCompiler()}
        toolchain._configured = True
        return toolchain

    def _env(self, monkeypatch, platform: Platform, runtime: str | None = None):
        from pcons.core.environment import Environment

        toolchain = self._toolchain(monkeypatch, platform)
        env = Environment()
        toolchain.setup(env)
        if runtime is not None:
            env.cuda.runtime = runtime
        return toolchain, env

    def _needs(self, toolchain, env, languages=frozenset({"cuda", "cxx"})):
        return toolchain.link_needs(None, set(languages), "cuda", env)

    def test_static_by_default_with_its_system_libraries(self, monkeypatch):
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM)
        assert self._needs(toolchain, env).libs == (
            "cudart_static",
            "dl",
            "rt",
            "pthread",
        )

    def test_shared(self, monkeypatch):
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM, "shared")
        assert self._needs(toolchain, env).libs == ("cudart",)

    def test_none(self, monkeypatch):
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM, "none")
        assert self._needs(toolchain, env).libs == ()

    def test_only_for_cuda_objects(self, monkeypatch):
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM)
        assert self._needs(toolchain, env, {"cxx"}).libs == ()

    def test_an_unknown_runtime_raises(self, monkeypatch):
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM, "Static")
        with pytest.raises(ValueError, match="'static', 'shared', 'none'"):
            self._needs(toolchain, env)

    def test_static_on_windows_leaves_out_libcmt(self, monkeypatch):
        """cudart_static.lib names the static release C runtime (LIBCMT),
        which a program with any other leaves out."""
        from pcons.toolchains.msvc import MsvcToolchain
        from pcons.tools.toolchain import LinkNeeds

        toolchain, env = self._env(monkeypatch, _WINDOWS_PLATFORM)
        needs = self._needs(toolchain, env)
        assert needs.libs == ("cudart_static",)
        assert needs.excluded_default_libs == ("LIBCMT",)
        flags = MsvcToolchain().realize_link_needs(
            LinkNeeds(excluded_default_libs=("LIBCMT",)), None, []
        )
        assert flags == ["/NODEFAULTLIB:LIBCMT"]
        env.set_runtime_linkage("static")
        assert self._needs(toolchain, env).excluded_default_libs == ()
        env.set_variant("debug")
        assert self._needs(toolchain, env).excluded_default_libs == ("LIBCMT",)

    def test_the_toolkit_libdir(self, monkeypatch, tmp_path):
        from pcons.toolchains import cuda as cuda_mod

        exe = ".exe" if sys.platform == "win32" else ""
        nvcc = tmp_path / "cuda" / "bin" / f"nvcc{exe}"
        nvcc.parent.mkdir(parents=True)
        nvcc.write_text("")
        nvcc.chmod(0o755)
        libdir = tmp_path / "cuda" / "lib64"
        libdir.mkdir()
        (libdir / "libcudart_static.a").write_text("")
        cuda_mod._runtime_libdir.cache_clear()
        toolchain, env = self._env(monkeypatch, _LINUX_PLATFORM)
        env.cuda.cmd = str(nvcc)
        assert self._needs(toolchain, env).libdirs == (str(libdir.resolve()),)

    @pytest.mark.parametrize(
        ("variant", "crt"), [("debug", "/MDd"), ("release", "/MD")]
    )
    def test_the_host_compiles_with_the_dll_c_runtime_on_windows(
        self, monkeypatch, variant, crt
    ):
        from pcons.core.environment import Environment

        env = Environment(toolchain=self._toolchain(monkeypatch, _WINDOWS_PLATFORM))
        assert "-Xcompiler=/MD" in env.cuda.flags
        env.set_variant(variant)
        assert f"-Xcompiler={crt}" in env.cuda.flags
        assert env.cuda.flags.count("-Xcompiler=/MD") == (variant != "debug")
