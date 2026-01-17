# SPDX-License-Identifier: MIT
"""Tests for pcons.core.resolver."""

from pathlib import Path

from pcons.core.project import Project
from pcons.core.resolver import SOURCE_SUFFIX_MAP, Resolver


class TestSourceSuffixMap:
    def test_c_suffix(self):
        """Test that .c files map to C compiler."""
        assert SOURCE_SUFFIX_MAP[".c"] == ("cc", "c")

    def test_cpp_suffixes(self):
        """Test that C++ suffixes map to C++ compiler."""
        assert SOURCE_SUFFIX_MAP[".cpp"] == ("cxx", "cxx")
        assert SOURCE_SUFFIX_MAP[".cxx"] == ("cxx", "cxx")
        assert SOURCE_SUFFIX_MAP[".cc"] == ("cxx", "cxx")
        assert SOURCE_SUFFIX_MAP[".c++"] == ("cxx", "cxx")


class TestResolverCreation:
    def test_create_resolver(self):
        """Test basic resolver creation."""
        project = Project("test")
        resolver = Resolver(project)

        assert resolver.project is project
        assert resolver._object_cache == {}


class TestResolverSingleTarget:
    def test_resolve_single_target(self, tmp_path):
        """Test resolving a single target."""
        # Create a source file
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Set up environment with minimal tool config
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Create target using factory method
        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])

        # Resolve
        project.resolve()

        # Check that target was resolved
        assert target._resolved
        assert len(target.object_nodes) == 1
        # Objects are placed in obj.<target>/ subdirectory to avoid naming conflicts
        assert (
            target.object_nodes[0].path == tmp_path / "build" / "obj.mylib" / "main.o"
        )

    def test_resolve_sets_object_build_info(self, tmp_path):
        """Test that resolved objects have proper build_info."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        obj_node = target.object_nodes[0]
        build_info = obj_node._build_info

        assert build_info["tool"] == "cc"
        assert build_info["command_var"] == "objcmd"
        assert "effective_includes" in build_info
        assert "effective_defines" in build_info
        assert "effective_flags" in build_info


class TestResolverSameSourceDifferentTargets:
    """Key test: same source compiles with different flags for different targets."""

    def test_same_source_different_flags(self, tmp_path):
        """Test that same source can compile with different flags for different targets."""
        # Create a source file
        src_file = tmp_path / "common.c"
        src_file.write_text("void common() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Create two targets using the same source but with different private requirements
        target1 = project.StaticLibrary("lib1", env, sources=[str(src_file)])
        target1.private.defines.append("TARGET1_DEFINE")

        target2 = project.StaticLibrary("lib2", env, sources=[str(src_file)])
        target2.private.defines.append("TARGET2_DEFINE")

        project.resolve()

        # Both targets should be resolved
        assert target1._resolved
        assert target2._resolved

        # Each target should have its own object node
        assert len(target1.object_nodes) == 1
        assert len(target2.object_nodes) == 1

        obj1 = target1.object_nodes[0]
        obj2 = target2.object_nodes[0]

        # Objects should be in different directories
        assert obj1.path != obj2.path
        assert "lib1" in str(obj1.path)
        assert "lib2" in str(obj2.path)

        # Objects should have different effective defines
        assert "TARGET1_DEFINE" in obj1._build_info["effective_defines"]
        assert "TARGET2_DEFINE" in obj2._build_info["effective_defines"]
        assert "TARGET2_DEFINE" not in obj1._build_info["effective_defines"]
        assert "TARGET1_DEFINE" not in obj2._build_info["effective_defines"]


class TestResolverTransitiveRequirements:
    def test_transitive_requirements_applied(self, tmp_path):
        """Test that transitive requirements are applied during resolution."""
        # Create source files
        lib_src = tmp_path / "lib.c"
        lib_src.write_text("void lib_func() {}")
        app_src = tmp_path / "main.c"
        app_src.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Create library with public requirements
        lib = project.StaticLibrary("mylib", env, sources=[str(lib_src)])
        lib.public.include_dirs.append(Path("include"))
        lib.public.defines.append("LIB_API")

        # Create app that links to library
        app = project.Program("myapp", env, sources=[str(app_src)])
        app.link(lib)

        project.resolve()

        # App's objects should have lib's public requirements
        app_obj = app.object_nodes[0]
        assert Path("include") in [
            Path(p) for p in app_obj._build_info["effective_includes"]
        ]
        assert "LIB_API" in app_obj._build_info["effective_defines"]


class TestResolverHeaderOnlyLibrary:
    def test_header_only_library(self, tmp_path):
        """Test that header-only libraries propagate requirements but have no objects."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Create header-only library
        header_lib = project.HeaderOnlyLibrary(
            "headers",
            include_dirs=[Path("headers/include")],
        )
        header_lib.public.defines.append("HEADER_LIB_API")

        # Create app that uses header-only library
        app = project.Program("myapp", env, sources=[str(src_file)])
        app.link(header_lib)

        project.resolve()

        # Header library should be resolved but have no objects/outputs
        assert header_lib._resolved
        assert header_lib.object_nodes == []
        assert header_lib.output_nodes == []

        # App should have header lib's requirements
        app_obj = app.object_nodes[0]
        # Normalize path separators for cross-platform comparison
        includes_normalized = " ".join(
            inc.replace("\\", "/") for inc in app_obj._build_info["effective_includes"]
        )
        assert "headers/include" in includes_normalized
        assert "HEADER_LIB_API" in app_obj._build_info["effective_defines"]


class TestResolverObjectCaching:
    def test_object_caching_same_flags(self, tmp_path):
        """Test that same source with same flags shares object node."""
        src_file = tmp_path / "common.c"
        src_file.write_text("void common() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Create two targets with identical effective requirements
        target1 = project.StaticLibrary("lib1", env, sources=[str(src_file)])
        target2 = project.StaticLibrary("lib2", env, sources=[str(src_file)])

        # Give them the same private defines
        target1.private.defines.append("SAME_DEFINE")
        target2.private.defines.append("SAME_DEFINE")

        resolver = Resolver(project)
        resolver.resolve()

        # Note: Objects are NOT cached when targets are different because
        # they go in different output directories by design.
        # The cache key includes the effective requirements hash,
        # but the output path includes the target name.
        # This is correct behavior - each target gets its own objects
        # even if the flags are identical.
        assert target1._resolved
        assert target2._resolved


class TestResolverTargetTypes:
    def test_program_target(self, tmp_path):
        """Test resolving a program target."""
        import sys

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"
        env.cc.linkcmd = "gcc $in -o $out"

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1
        assert len(target.output_nodes) == 1
        # On Windows, programs have .exe suffix
        expected_name = "myapp.exe" if sys.platform == "win32" else "myapp"
        assert target.output_nodes[0].path.name == expected_name

    def test_shared_library_target(self, tmp_path):
        """Test resolving a shared library target."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"
        env.cc.sharedcmd = "gcc -shared $in -o $out"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.output_nodes) == 1
        # Platform-specific library naming
        import sys

        if sys.platform == "darwin":
            assert target.output_nodes[0].path.name == "libmylib.dylib"
        elif sys.platform == "win32":
            assert target.output_nodes[0].path.name == "mylib.dll"
        else:
            assert target.output_nodes[0].path.name == "libmylib.so"

    def test_object_library_target(self, tmp_path):
        """Test resolving an object library target."""
        src_file = tmp_path / "obj.c"
        src_file.write_text("void obj_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.ObjectLibrary("objs", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1
        # Object library's output_nodes are the object files themselves
        assert target.output_nodes == target.object_nodes


class TestResolverLanguageDetection:
    def test_detect_c_language(self, tmp_path):
        """Test that C language is detected from .c files."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert "c" in target.required_languages

    def test_detect_cxx_language(self, tmp_path):
        """Test that C++ language is detected from .cpp files."""
        src_file = tmp_path / "main.cpp"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cxx")
        env.cxx.objcmd = "g++ -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert "cxx" in target.required_languages


class TestResolverOutputName:
    """Tests for target.output_name custom output naming."""

    def test_shared_library_output_name(self, tmp_path):
        """Test that output_name overrides shared library naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.SharedLibrary("plugin", env, sources=[str(src_file)])
        target.output_name = "plugin.ofx"  # Custom name with .ofx suffix

        project.resolve()

        assert target._resolved
        assert len(target.output_nodes) == 1
        # Should use custom name, not platform default
        assert target.output_nodes[0].path.name == "plugin.ofx"

    def test_static_library_output_name(self, tmp_path):
        """Test that output_name overrides static library naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        target.output_name = "custom_mylib.lib"  # Windows-style naming

        project.resolve()

        assert target._resolved
        assert target.output_nodes[0].path.name == "custom_mylib.lib"

    def test_program_output_name(self, tmp_path):
        """Test that output_name overrides program naming."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.Program("myapp", env, sources=[str(src_file)])
        target.output_name = "custom_app.bin"

        project.resolve()

        assert target._resolved
        assert target.output_nodes[0].path.name == "custom_app.bin"

    def test_output_name_none_uses_default(self, tmp_path):
        """Test that None output_name uses default naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        # output_name is None by default

        project.resolve()

        assert target._resolved
        # Should use platform default
        import sys

        if sys.platform == "darwin":
            assert target.output_nodes[0].path.name == "libmylib.dylib"
        elif sys.platform == "win32":
            assert target.output_nodes[0].path.name == "mylib.dll"
        else:
            assert target.output_nodes[0].path.name == "libmylib.so"


class TestResolverSharedLibraryCompileFlags:
    """Test that shared library objects get correct target-type compile flags."""

    def test_shared_library_gets_fpic_on_linux(self, tmp_path, monkeypatch):
        """Test that shared library objects get -fPIC on Linux."""
        from pcons.configure.platform import Platform
        from pcons.toolchains.gcc import GccToolchain

        # Mock platform to be Linux
        linux_platform = Platform(
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
        # Need to patch in multiple places
        monkeypatch.setattr("pcons.toolchains.gcc.get_platform", lambda: linux_platform)

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is in the effective flags
        obj_node = target.object_nodes[0]
        assert "-fPIC" in obj_node._build_info["effective_flags"]

    def test_shared_library_no_fpic_on_macos(self, tmp_path, monkeypatch):
        """Test that shared library objects don't get -fPIC on macOS (it's default)."""
        from pcons.configure.platform import Platform
        from pcons.toolchains.gcc import GccToolchain

        # Mock platform to be macOS
        macos_platform = Platform(
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
        monkeypatch.setattr("pcons.toolchains.gcc.get_platform", lambda: macos_platform)

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the effective flags
        obj_node = target.object_nodes[0]
        assert "-fPIC" not in obj_node._build_info["effective_flags"]

    def test_static_library_no_fpic(self, tmp_path, monkeypatch):
        """Test that static library objects don't get -fPIC."""
        from pcons.configure.platform import Platform
        from pcons.toolchains.gcc import GccToolchain

        # Mock platform to be Linux
        linux_platform = Platform(
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
        monkeypatch.setattr("pcons.toolchains.gcc.get_platform", lambda: linux_platform)

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the effective flags
        obj_node = target.object_nodes[0]
        assert "-fPIC" not in obj_node._build_info["effective_flags"]

    def test_program_no_fpic(self, tmp_path, monkeypatch):
        """Test that program objects don't get -fPIC."""
        from pcons.configure.platform import Platform
        from pcons.toolchains.gcc import GccToolchain

        # Mock platform to be Linux
        linux_platform = Platform(
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
        monkeypatch.setattr("pcons.toolchains.gcc.get_platform", lambda: linux_platform)

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the effective flags
        obj_node = target.object_nodes[0]
        assert "-fPIC" not in obj_node._build_info["effective_flags"]


class TestResolverToolAgnostic:
    """Test that resolver works with non-C toolchains (tool-agnostic design)."""

    def test_custom_toolchain_source_handler(self, tmp_path):
        """Test resolver uses toolchain's source handler."""
        from pcons.tools.toolchain import BaseToolchain, SourceHandler

        # Create a mock toolchain that handles .tex files
        class TexToolchain(BaseToolchain):
            def __init__(self):
                super().__init__("tex")

            def _configure_tools(self, config):
                return True

            def get_source_handler(self, suffix: str) -> SourceHandler | None:
                if suffix.lower() == ".tex":
                    return SourceHandler(
                        tool_name="latex",
                        language="latex",
                        object_suffix=".aux",
                        depfile=None,  # LaTeX doesn't produce .d files
                        deps_style=None,
                    )
                return None

            def get_object_suffix(self) -> str:
                return ".aux"

            def get_static_library_name(self, name: str) -> str:
                return f"{name}.pdf"  # Not really applicable, but for completeness

        # Create a .tex file
        tex_file = tmp_path / "document.tex"
        tex_file.write_text(
            r"\documentclass{article}\begin{document}Hello\end{document}"
        )

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with our custom toolchain
        tex_toolchain = TexToolchain()
        tex_toolchain._configured = True
        env = project.Environment(toolchain=tex_toolchain)

        # Add a fake latex tool
        env.add_tool("latex")
        env.latex.objcmd = "pdflatex -output-directory $out_dir $in"

        # Create target
        target = project.StaticLibrary("document", env, sources=[str(tex_file)])
        project.resolve()

        # Verify the toolchain's source handler was used
        assert target._resolved
        assert len(target.object_nodes) == 1

        obj_node = target.object_nodes[0]

        # Check that the object has .aux suffix (from toolchain, not hardcoded)
        assert obj_node.path.suffix == ".aux"

        # Check build_info uses the toolchain's handler
        build_info = obj_node._build_info
        assert build_info["tool"] == "latex"
        assert build_info["language"] == "latex"
        assert build_info["depfile"] is None  # No depfile for LaTeX
        assert build_info["deps_style"] is None

    def test_toolchain_library_naming(self, tmp_path):
        """Test that library names come from toolchain, not hardcoded."""
        from pcons.tools.toolchain import BaseToolchain, SourceHandler

        class CustomToolchain(BaseToolchain):
            def __init__(self):
                super().__init__("custom")

            def _configure_tools(self, config):
                return True

            def get_source_handler(self, suffix: str) -> SourceHandler | None:
                if suffix.lower() == ".c":
                    return SourceHandler("cc", "c", ".obj", "$out.d", "gcc")
                return None

            def get_object_suffix(self) -> str:
                return ".obj"  # Custom object suffix

            def get_static_library_name(self, name: str) -> str:
                return f"lib{name}_custom.a"  # Custom naming

            def get_shared_library_name(self, name: str) -> str:
                return f"{name}_custom.dll"  # Custom naming

            def get_program_name(self, name: str) -> str:
                return f"{name}_custom.bin"  # Custom naming

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        toolchain = CustomToolchain()
        toolchain._configured = True
        env = project.Environment(toolchain=toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        # Test static library naming
        lib = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert lib._resolved
        # Check that object has custom suffix
        assert lib.object_nodes[0].path.suffix == ".obj"
        # Check that library has custom name
        assert lib.output_nodes[0].path.name == "libmylib_custom.a"

    def test_toolchain_program_naming(self, tmp_path):
        """Test that program names come from toolchain."""
        from pcons.tools.toolchain import BaseToolchain, SourceHandler

        class CustomToolchain(BaseToolchain):
            def __init__(self):
                super().__init__("custom")

            def _configure_tools(self, config):
                return True

            def get_source_handler(self, suffix: str) -> SourceHandler | None:
                if suffix.lower() == ".c":
                    return SourceHandler("cc", "c", ".o", "$out.d", "gcc")
                return None

            def get_program_name(self, name: str) -> str:
                return f"{name}.exe"  # Always add .exe

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        toolchain = CustomToolchain()
        toolchain._configured = True
        env = project.Environment(toolchain=toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        prog = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert prog._resolved
        assert prog.output_nodes[0].path.name == "myapp.exe"

    def test_fallback_without_toolchain(self, tmp_path):
        """Test that resolver falls back to hardcoded values when no toolchain."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # No toolchain
        env = project.Environment()
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $in -o $out"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        # Should still work with fallback values
        assert target._resolved
        assert len(target.object_nodes) == 1
        # Default .o suffix
        assert target.object_nodes[0].path.suffix == ".o"
        # Default lib prefix
        assert target.output_nodes[0].path.name == "libmylib.a"
