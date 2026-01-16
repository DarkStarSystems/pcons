# SPDX-License-Identifier: MIT
"""Tests for Cython toolchain."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcons.toolchains.cython import (
    CythonCCompiler,
    CythonLinker,
    CythonToolchain,
    CythonTranspiler,
    get_python_info,
)


class TestGetPythonInfo:
    """Tests for get_python_info function."""

    def test_returns_dict(self) -> None:
        """Test that get_python_info returns expected keys."""
        info = get_python_info()
        assert "include_dir" in info
        assert "ext_suffix" in info
        assert "lib_dir" in info
        assert "libs" in info

    def test_ext_suffix_format(self) -> None:
        """Test extension suffix has expected format."""
        info = get_python_info()
        ext = info["ext_suffix"]
        # Should end with .so, .pyd, or .dylib (depending on platform)
        assert ext.endswith((".so", ".pyd", ".dylib"))

    def test_include_dir_exists(self) -> None:
        """Test that include directory exists."""
        info = get_python_info()
        if info["include_dir"]:
            assert Path(info["include_dir"]).exists()


class TestCythonTranspiler:
    """Tests for CythonTranspiler tool."""

    def test_tool_name(self) -> None:
        """Test tool name."""
        tool = CythonTranspiler()
        assert tool.name == "cython"

    def test_default_vars(self) -> None:
        """Test default variable values."""
        tool = CythonTranspiler()
        defaults = tool.default_vars()
        assert "cmd" in defaults
        assert "flags" in defaults
        assert "pyx_to_c_cmd" in defaults
        assert defaults["cmd"] == "cython"

    def test_builders(self) -> None:
        """Test that Transpile builder is provided."""
        tool = CythonTranspiler()
        builders = tool.builders()
        assert "Transpile" in builders
        transpile = builders["Transpile"]
        assert transpile.src_suffixes == [".pyx"]
        assert transpile.target_suffixes == [".c"]


class TestCythonCCompiler:
    """Tests for CythonCCompiler tool."""

    def test_tool_name(self) -> None:
        """Test tool name."""
        tool = CythonCCompiler()
        assert tool.name == "cycc"

    def test_default_vars_includes_python(self) -> None:
        """Test that default vars include Python headers."""
        tool = CythonCCompiler()
        defaults = tool.default_vars()
        includes = defaults.get("includes", [])
        # iprefix holds the -I prefix, includes holds the paths without -I
        assert defaults.get("iprefix") == "-I"
        # Should have at least one include path (Python headers)
        # On some systems this might be empty if include_dir is not found
        if includes:
            assert all(isinstance(inc, str) for inc in includes)
            # Paths should not have -I prefix (that's in iprefix)
            assert not any(str(inc).startswith("-I") for inc in includes)

    def test_has_fpic_flag(self) -> None:
        """Test that -fPIC flag is included."""
        tool = CythonCCompiler()
        defaults = tool.default_vars()
        flags = defaults.get("flags", [])
        assert "-fPIC" in flags


class TestCythonLinker:
    """Tests for CythonLinker tool."""

    def test_tool_name(self) -> None:
        """Test tool name."""
        tool = CythonLinker()
        assert tool.name == "cylink"

    def test_extension_builder(self) -> None:
        """Test that Extension builder is provided."""
        tool = CythonLinker()
        builders = tool.builders()
        assert "Extension" in builders
        ext = builders["Extension"]
        # Target suffix should be Python extension suffix
        info = get_python_info()
        assert ext.target_suffixes == [info["ext_suffix"]]


class TestCythonToolchain:
    """Tests for CythonToolchain."""

    def test_toolchain_name(self) -> None:
        """Test toolchain name."""
        toolchain = CythonToolchain()
        assert toolchain.name == "cython"

    def test_has_all_tools(self) -> None:
        """Test that toolchain provides all expected tools."""
        # Can't easily test configure without mocking, but we can test
        # that the tools are instantiated correctly
        transpiler = CythonTranspiler()
        compiler = CythonCCompiler()
        linker = CythonLinker()

        assert transpiler.name == "cython"
        assert compiler.name == "cycc"
        assert linker.name == "cylink"


@pytest.mark.skipif(
    shutil.which("cython") is None and shutil.which("cython3") is None,
    reason="Cython not available",
)
class TestCythonIntegration:
    """Integration tests for Cython toolchain."""

    def test_transpile_simple_pyx(self, tmp_path: Path) -> None:
        """Test transpiling a simple .pyx file."""
        # Create a simple .pyx file
        pyx_file = tmp_path / "hello.pyx"
        pyx_file.write_text(
            """\
def say_hello(name):
    return f"Hello, {name}!"
"""
        )

        # Find cython command
        cython_cmd = shutil.which("cython") or shutil.which("cython3")
        assert cython_cmd is not None

        # Transpile to C
        c_file = tmp_path / "hello.c"
        result = subprocess.run(
            [cython_cmd, "-o", str(c_file), str(pyx_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Cython failed: {result.stderr}"
        assert c_file.exists()

        # Check that C file contains expected content
        c_content = c_file.read_text()
        assert "say_hello" in c_content
        assert "#include" in c_content

    @pytest.mark.skipif(
        shutil.which("clang") is None and shutil.which("gcc") is None,
        reason="C compiler not available",
    )
    def test_full_extension_build(self, tmp_path: Path) -> None:
        """Test building a complete Python extension."""
        import sysconfig

        # Create a simple .pyx file
        pyx_file = tmp_path / "greet.pyx"
        pyx_file.write_text(
            """\
def greet(name):
    return f"Hello from Cython, {name}!"
"""
        )

        # Find tools
        cython_cmd = shutil.which("cython") or shutil.which("cython3")
        cc_cmd = shutil.which("clang") or shutil.which("gcc")
        assert cython_cmd is not None
        assert cc_cmd is not None

        # Get Python info
        include_dir = sysconfig.get_path("include")
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"

        # Step 1: Transpile .pyx to .c
        c_file = tmp_path / "greet.c"
        result = subprocess.run(
            [cython_cmd, "-o", str(c_file), str(pyx_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Cython failed: {result.stderr}"

        # Step 2: Compile .c to .o
        obj_file = tmp_path / "greet.o"
        compile_cmd = [
            cc_cmd,
            "-fPIC",
            f"-I{include_dir}",
            "-c",
            "-o",
            str(obj_file),
            str(c_file),
        ]
        result = subprocess.run(compile_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Compile failed: {result.stderr}"

        # Step 3: Link to shared library
        ext_file = tmp_path / f"greet{ext_suffix}"

        # Platform-specific link flags
        if sys.platform == "darwin":
            link_flags = ["-bundle", "-undefined", "dynamic_lookup"]
        else:
            link_flags = ["-shared"]

        link_cmd = [cc_cmd] + link_flags + ["-o", str(ext_file), str(obj_file)]
        result = subprocess.run(link_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Link failed: {result.stderr}"
        assert ext_file.exists()

        # Step 4: Try to import and use the extension
        # Add tmp_path to sys.path and import
        sys.path.insert(0, str(tmp_path))
        try:
            import greet  # type: ignore[import-not-found]

            result_str = greet.greet("World")
            assert result_str == "Hello from Cython, World!"
        finally:
            sys.path.remove(str(tmp_path))
            # Clean up the imported module
            if "greet" in sys.modules:
                del sys.modules["greet"]
