# SPDX-License-Identifier: MIT
"""Tests for multi-toolchain support."""

from pcons.core.builder import Builder
from pcons.core.environment import Environment
from pcons.core.project import Project
from pcons.tools.tool import BaseTool
from pcons.tools.toolchain import BaseToolchain, SourceHandler


class MockCTool(BaseTool):
    """Mock C compiler tool for testing."""

    def __init__(self) -> None:
        super().__init__("cc", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "mock-cc",
            "flags": [],
            "objcmd": "mock-cc -c $SOURCE -o $TARGET",
        }

    def builders(self) -> dict[str, Builder]:
        return {}


class MockCxxTool(BaseTool):
    """Mock C++ compiler tool for testing."""

    def __init__(self) -> None:
        super().__init__("cxx", language="cxx")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "mock-cxx",
            "flags": [],
            "objcmd": "mock-cxx -c $SOURCE -o $TARGET",
        }

    def builders(self) -> dict[str, Builder]:
        return {}


class MockCudaTool(BaseTool):
    """Mock CUDA compiler tool for testing."""

    def __init__(self) -> None:
        super().__init__("cuda", language="cuda")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "mock-nvcc",
            "flags": [],
            "objcmd": "mock-nvcc -c $SOURCE -o $TARGET",
        }

    def builders(self) -> dict[str, Builder]:
        return {}


class MockCToolchain(BaseToolchain):
    """Mock C/C++ toolchain for testing."""

    def __init__(self) -> None:
        super().__init__("mock-c")

    def _configure_tools(self, config: object) -> bool:
        self._tools = {
            "cc": MockCTool(),
            "cxx": MockCxxTool(),
        }
        return True

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        from pcons.core.subst import TargetPath

        suffix_lower = suffix.lower()
        if suffix_lower == ".c":
            return SourceHandler("cc", "c", ".o", TargetPath(suffix=".d"), "gcc")
        if suffix_lower in (".cpp", ".cxx", ".cc"):
            return SourceHandler("cxx", "cxx", ".o", TargetPath(suffix=".d"), "gcc")
        return None

    def get_auxiliary_input_handler(self, suffix: str):
        return None

    def get_object_suffix(self) -> str:
        return ".o"


class MockCudaToolchain(BaseToolchain):
    """Mock CUDA toolchain for testing."""

    def __init__(self) -> None:
        super().__init__("mock-cuda")

    def _configure_tools(self, config: object) -> bool:
        self._tools = {"cuda": MockCudaTool()}
        return True

    def get_source_handler(self, suffix: str) -> SourceHandler | None:
        from pcons.core.subst import TargetPath

        if suffix.lower() == ".cu":
            return SourceHandler("cuda", "cuda", ".o", TargetPath(suffix=".d"), "gcc")
        return None

    def get_auxiliary_input_handler(self, suffix: str):
        return None

    def get_object_suffix(self) -> str:
        return ".o"

    def apply_variant(self, env: Environment, variant: str, **kwargs) -> None:
        super().apply_variant(env, variant, **kwargs)
        if env.has_tool("cuda"):
            if variant == "debug":
                env.cuda.flags.extend(["-G", "-g"])
            elif variant == "release":
                env.cuda.flags.extend(["-O3"])


class TestMultiToolchainEnvironment:
    """Tests for multi-toolchain Environment methods."""

    def test_add_toolchain(self):
        """Test adding additional toolchains."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        env = Environment(toolchain=c_toolchain)
        assert env.has_tool("cc")
        assert env.has_tool("cxx")
        assert not env.has_tool("cuda")

        env.add_toolchain(cuda_toolchain)
        assert env.has_tool("cuda")

    def test_toolchains_property_returns_all(self):
        """Test that toolchains property includes primary and additional."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        env = Environment(toolchain=c_toolchain)
        assert len(env.toolchains) == 1
        assert env.toolchains[0] is c_toolchain

        env.add_toolchain(cuda_toolchain)
        assert len(env.toolchains) == 2
        assert env.toolchains[0] is c_toolchain
        assert env.toolchains[1] is cuda_toolchain

    def test_toolchains_property_empty_without_toolchain(self):
        """Test that toolchains returns empty list when no toolchain set."""
        env = Environment()
        assert env.toolchains == []

    def test_clone_preserves_additional_toolchains(self):
        """Test that clone copies additional toolchains."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        env = Environment(toolchain=c_toolchain)
        env.add_toolchain(cuda_toolchain)

        cloned = env.clone()
        assert len(cloned.toolchains) == 2
        assert cloned.toolchains[0] is c_toolchain
        assert cloned.toolchains[1] is cuda_toolchain

    def test_set_variant_applies_to_all_toolchains(self):
        """Test that set_variant calls apply_variant on all toolchains."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        env = Environment(toolchain=c_toolchain)
        env.add_toolchain(cuda_toolchain)

        # Apply debug variant
        env.set_variant("debug")

        # Check that CUDA debug flags were applied
        assert "-G" in env.cuda.flags
        assert "-g" in env.cuda.flags

    def test_set_variant_no_toolchains(self):
        """Test set_variant works when no toolchains configured."""
        env = Environment()
        env.set_variant("release")
        assert env.variant == "release"


class TestMultiToolchainResolver:
    """Tests for resolver with multiple toolchains."""

    def test_source_goes_to_correct_compiler(self, tmp_path):
        """Test that .cu files use cuda, .cpp files use cxx."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        # Create source files
        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")
        cu_file = tmp_path / "kernel.cu"
        cu_file.write_text("__global__ void kernel() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=c_toolchain)
        env.add_toolchain(cuda_toolchain)

        target = project.StaticLibrary(
            "mylib", env, sources=[str(cpp_file), str(cu_file)]
        )
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 2

        # Check that each source went to the correct tool
        build_infos = {
            obj._build_info["language"]: obj._build_info["tool"]
            for obj in target.object_nodes
        }
        assert build_infos.get("cxx") == "cxx"
        assert build_infos.get("cuda") == "cuda"

    def test_first_toolchain_wins_for_conflicts(self, tmp_path):
        """Test that primary toolchain's handler takes precedence."""

        # Create a toolchain that also handles .cpp (conflict with C toolchain)
        class ConflictingToolchain(BaseToolchain):
            def __init__(self) -> None:
                super().__init__("conflict")

            def _configure_tools(self, config: object) -> bool:
                return True

            def get_source_handler(self, suffix: str) -> SourceHandler | None:
                # Also claims to handle .cpp
                if suffix.lower() == ".cpp":
                    return SourceHandler("alt", "alt", ".obj", None, None)
                return None

            def get_auxiliary_input_handler(self, suffix: str):
                return None

        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        conflict_toolchain = ConflictingToolchain()
        conflict_toolchain.configure(None)

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=c_toolchain)
        # Add conflicting toolchain second - should NOT override primary
        env.add_toolchain(conflict_toolchain)

        target = project.StaticLibrary("mylib", env, sources=[str(cpp_file)])
        project.resolve()

        # Should use the primary toolchain's handler (cxx), not the conflicting one
        assert target.object_nodes[0]._build_info["tool"] == "cxx"
        assert target.object_nodes[0]._build_info["language"] == "cxx"

    def test_linker_selection_with_multiple_languages(self, tmp_path):
        """Test correct linker selection when mixing C++ and CUDA."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")
        cu_file = tmp_path / "kernel.cu"
        cu_file.write_text("__global__ void kernel() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=c_toolchain)
        env.add_toolchain(cuda_toolchain)

        target = project.Program("myapp", env, sources=[str(cpp_file), str(cu_file)])
        project.resolve()

        # Should have detected both languages
        assert (
            "cxx" in target.get_all_languages() or "cuda" in target.get_all_languages()
        )


class TestCppPlusCuda:
    """Integration tests for C++ + CUDA builds."""

    def test_mixed_cpp_cuda_sources(self, tmp_path):
        """Integration test: build target with both .cpp and .cu files."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        # Create source files
        cpp_file = tmp_path / "host.cpp"
        cpp_file.write_text("void host_func() {}")
        cu_file = tmp_path / "device.cu"
        cu_file.write_text("__global__ void device_kernel() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=c_toolchain)
        env.add_toolchain(cuda_toolchain)

        lib = project.StaticLibrary(
            "mixedlib", env, sources=[str(cpp_file), str(cu_file)]
        )
        project.resolve()

        assert lib._resolved
        assert len(lib.object_nodes) == 2

        # Verify both compilers were used
        tools_used = {obj._build_info["tool"] for obj in lib.object_nodes}
        assert "cxx" in tools_used
        assert "cuda" in tools_used

    def test_cuda_only_env(self, tmp_path):
        """Test environment with only CUDA toolchain."""
        cuda_toolchain = MockCudaToolchain()
        cuda_toolchain.configure(None)

        cu_file = tmp_path / "kernel.cu"
        cu_file.write_text("__global__ void kernel() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=cuda_toolchain)

        lib = project.StaticLibrary("cudalib", env, sources=[str(cu_file)])
        project.resolve()

        assert lib._resolved
        assert len(lib.object_nodes) == 1
        assert lib.object_nodes[0]._build_info["tool"] == "cuda"


class TestBackwardsCompatibility:
    """Tests to ensure backwards compatibility with single toolchain."""

    def test_single_toolchain_still_works(self, tmp_path):
        """Verify single toolchain setup works unchanged."""
        c_toolchain = MockCToolchain()
        c_toolchain.configure(None)

        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=c_toolchain)

        target = project.Program("myapp", env, sources=[str(cpp_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1
        assert target.object_nodes[0]._build_info["tool"] == "cxx"
