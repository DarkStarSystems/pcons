# SPDX-License-Identifier: MIT
"""Tests for CUDA toolchain."""

from pcons.toolchains import CudaCompiler, CudaToolchain, find_cuda_toolchain
from pcons.tools.toolchain import SourceHandler


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
        assert handler.object_suffix == ".o"

    def test_source_handler_unknown(self):
        """CudaToolchain returns None for unknown suffixes."""
        toolchain = CudaToolchain()
        assert toolchain.get_source_handler(".c") is None
        assert toolchain.get_source_handler(".cpp") is None
        assert toolchain.get_source_handler(".txt") is None

    def test_object_suffix(self):
        """CudaToolchain produces .o files."""
        toolchain = CudaToolchain()
        assert toolchain.get_object_suffix() == ".o"


class TestCudaVariants:
    """Tests for CUDA variant support."""

    def test_apply_debug_variant(self):
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

    def test_apply_release_variant(self):
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

    def test_apply_profile_variant(self):
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
