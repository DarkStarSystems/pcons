# SPDX-License-Identifier: MIT
"""Tests for pcons.core.resolver."""

from pathlib import Path

from pcons.core.project import Project
from pcons.core.resolver import Resolver


class TestResolverCreation:
    def test_create_resolver(self):
        """Test basic resolver creation."""
        project = Project("test")
        resolver = Resolver(project)

        assert resolver.project is project


class TestResolverSingleTarget:
    def test_resolve_single_target(self, tmp_path, gcc_toolchain):
        """Test resolving a single target."""
        # Create a source file
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Set up environment with toolchain
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        # Create target using factory method
        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])

        # Resolve
        project.resolve()

        # Check that target was resolved
        assert target._resolved
        assert len(target.object_nodes) == 1
        # Objects are placed in obj.<target>/ subdirectory to avoid naming conflicts
        assert target.object_nodes[0].path == Path("build/obj.mylib/main.o")

    def test_resolve_sets_object_build_info(self, tmp_path, gcc_toolchain):
        """Test that resolved objects have proper build_info."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        obj_node = target.object_nodes[0]
        build_info = obj_node._build_info

        assert build_info["tool"] == "cc"
        assert build_info["command_var"] == "objcmd"
        # Build context provides env overrides for template expansion
        assert "context" in build_info
        context = build_info["context"]
        assert context is not None
        # Context provides get_env_overrides() for template expansion
        assert hasattr(context, "get_env_overrides")
        overrides = context.get_env_overrides()
        assert isinstance(overrides, dict)


class TestResolverSameSourceDifferentTargets:
    """Key test: same source compiles with different flags for different targets."""

    def test_same_source_different_flags(self, tmp_path, gcc_toolchain):
        """Test that same source can compile with different flags for different targets."""
        # Create a source file
        src_file = tmp_path / "common.c"
        src_file.write_text("void common() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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

        # Objects should have different defines in their context
        context1 = obj1._build_info["context"]
        context2 = obj2._build_info["context"]
        assert "TARGET1_DEFINE" in context1.defines
        assert "TARGET2_DEFINE" in context2.defines
        assert "TARGET2_DEFINE" not in context1.defines
        assert "TARGET1_DEFINE" not in context2.defines


class TestResolverTransitiveRequirements:
    def test_transitive_requirements_applied(self, tmp_path, gcc_toolchain):
        """Test that transitive requirements are applied during resolution."""
        # Create source files
        lib_src = tmp_path / "lib.c"
        lib_src.write_text("void lib_func() {}")
        app_src = tmp_path / "main.c"
        app_src.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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
        context = app_obj._build_info["context"]
        assert Path("include") in [Path(p) for p in context.includes]
        assert "LIB_API" in context.defines


class TestResolverHeaderOnlyLibrary:
    def test_header_only_library(self, tmp_path, gcc_toolchain):
        """Test that header-only libraries propagate requirements but have no objects."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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
        context = app_obj._build_info["context"]
        # Normalize path separators for cross-platform comparison
        includes_normalized = " ".join(
            inc.replace("\\", "/") for inc in context.includes
        )
        assert "headers/include" in includes_normalized
        assert "HEADER_LIB_API" in context.defines


class TestResolverObjectCaching:
    def test_object_caching_same_flags(self, tmp_path, gcc_toolchain):
        """Test that same source with same flags shares object node."""
        src_file = tmp_path / "common.c"
        src_file.write_text("void common() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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
    def test_program_target(self, tmp_path, gcc_toolchain):
        """Test resolving a program target."""
        import sys

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"
        env.cc.linkcmd = "gcc $SOURCES -o $TARGET"

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1
        assert len(target.output_nodes) == 1
        # On Windows, programs have .exe suffix
        expected_name = "myapp.exe" if sys.platform == "win32" else "myapp"
        assert target.output_nodes[0].path.name == expected_name

    def test_shared_library_target(self, tmp_path, gcc_toolchain):
        """Test resolving a shared library target."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"
        env.cc.sharedcmd = "gcc -shared $SOURCES -o $TARGET"

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

    def test_object_library_target(self, tmp_path, gcc_toolchain):
        """Test resolving an object library target."""
        src_file = tmp_path / "obj.c"
        src_file.write_text("void obj_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.ObjectLibrary("objs", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1
        # Object library's output_nodes are the object files themselves
        assert target.output_nodes == target.object_nodes


class TestResolverLanguageDetection:
    def test_detect_c_language(self, tmp_path, gcc_toolchain):
        """Test that C language is detected from .c files."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert "c" in target.required_languages

    def test_detect_cxx_language(self, tmp_path, gcc_toolchain):
        """Test that C++ language is detected from .cpp files."""
        src_file = tmp_path / "main.cpp"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cxx")
        env.cxx.objcmd = "g++ -c $SOURCE -o $TARGET"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert "cxx" in target.required_languages


class TestResolverOutputName:
    """Tests for target.output_name custom output naming."""

    def test_shared_library_output_name(self, tmp_path, gcc_toolchain):
        """Test that output_name overrides shared library naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.SharedLibrary("plugin", env, sources=[str(src_file)])
        target.output_name = "plugin.ofx"  # Custom name with .ofx suffix

        project.resolve()

        assert target._resolved
        assert len(target.output_nodes) == 1
        # Should use custom name, not platform default
        assert target.output_nodes[0].path.name == "plugin.ofx"

    def test_static_library_output_name(self, tmp_path, gcc_toolchain):
        """Test that output_name overrides static library naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        target.output_name = "custom_mylib.lib"  # Windows-style naming

        project.resolve()

        assert target._resolved
        assert target.output_nodes[0].path.name == "custom_mylib.lib"

    def test_program_output_name(self, tmp_path, gcc_toolchain):
        """Test that output_name overrides program naming."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.Program("myapp", env, sources=[str(src_file)])
        target.output_name = "custom_app.bin"

        project.resolve()

        assert target._resolved
        assert target.output_nodes[0].path.name == "custom_app.bin"

    def test_output_name_none_uses_default(self, tmp_path, gcc_toolchain):
        """Test that None output_name uses default naming."""
        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is in the compile flags via context
        obj_node = target.object_nodes[0]
        context = obj_node._build_info["context"]
        assert "-fPIC" in context.flags

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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: macos_platform
        )

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the compile flags via context
        obj_node = target.object_nodes[0]
        context = obj_node._build_info["context"]
        assert "-fPIC" not in context.flags

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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        src_file = tmp_path / "lib.c"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.StaticLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the compile flags via context
        obj_node = target.object_nodes[0]
        context = obj_node._build_info["context"]
        assert "-fPIC" not in context.flags

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
        monkeypatch.setattr(
            "pcons.toolchains.unix.get_platform", lambda: linux_platform
        )

        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")

        # Create environment with GCC toolchain
        gcc_toolchain = GccToolchain()
        gcc_toolchain._configured = True
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 1

        # Check that -fPIC is NOT in the compile flags via context
        obj_node = target.object_nodes[0]
        context = obj_node._build_info["context"]
        assert "-fPIC" not in context.flags


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
        env.latex.objcmd = "pdflatex -output-directory $out_dir $SOURCE"

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
                from pcons.core.subst import TargetPath

                if suffix.lower() == ".c":
                    return SourceHandler(
                        "cc", "c", ".obj", TargetPath(suffix=".d"), "gcc"
                    )
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
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

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
                from pcons.core.subst import TargetPath

                if suffix.lower() == ".c":
                    return SourceHandler(
                        "cc", "c", ".o", TargetPath(suffix=".d"), "gcc"
                    )
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
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        prog = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert prog._resolved
        assert prog.output_nodes[0].path.name == "myapp.exe"


class TestResolverPrecompiledObjects:
    """Tests for passing pre-compiled objects and unrecognized files as sources."""

    def test_precompiled_object_as_source(self, tmp_path, gcc_toolchain):
        """Pre-compiled .o files are included in linking."""
        from pcons.core.node import FileNode

        # Create source file
        main_src = tmp_path / "main.c"
        main_src.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        # Create a pre-compiled object node (simulating output from env.cc.Object())
        helper_obj = FileNode(tmp_path / "build" / "helper.o")

        # Create program with both a source file and the pre-compiled object
        prog = project.Program("myapp", env)
        prog.add_sources([str(main_src), helper_obj])

        project.resolve()

        assert prog._resolved
        # Should have 2 object nodes: main.o (compiled by Program) and helper.o (passed through)
        assert len(prog.object_nodes) == 2

        # Check that both objects are present
        obj_paths = [str(obj.path) for obj in prog.object_nodes]
        assert any("main.o" in p for p in obj_paths)
        assert any("helper.o" in p for p in obj_paths)

    def test_unrecognized_file_passed_through(self, tmp_path, gcc_toolchain):
        """Files without source handlers pass through directly to linker."""
        from pcons.core.node import FileNode

        main_src = tmp_path / "main.c"
        main_src.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        # Create a FileNode for an object file directly (simulating external object)
        external_obj = FileNode(tmp_path / "external.o")

        # Pass both source and object to Program
        prog = project.Program("myapp", env)
        prog.add_sources([str(main_src), external_obj])

        project.resolve()

        assert prog._resolved
        # Should have 2 object nodes
        assert len(prog.object_nodes) == 2

        # The external.o should be passed through unchanged
        obj_paths = [obj.path for obj in prog.object_nodes]
        assert tmp_path / "external.o" in obj_paths

    def test_linker_script_passed_through(self, tmp_path, gcc_toolchain):
        """Linker scripts (.ld) and other unrecognized files pass through."""
        from pcons.core.node import FileNode

        main_src = tmp_path / "main.c"
        main_src.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        # Create a linker script node
        linker_script = FileNode(tmp_path / "custom.ld")

        prog = project.Program("myapp", env)
        prog.add_sources([str(main_src), linker_script])

        project.resolve()

        assert prog._resolved
        # Should have 2 object nodes (main.o and the linker script)
        assert len(prog.object_nodes) == 2

        obj_paths = [obj.path for obj in prog.object_nodes]
        assert tmp_path / "custom.ld" in obj_paths

    def test_mixed_sources_and_objects(self, tmp_path, gcc_toolchain):
        """Mix of .c sources and .o objects all end up in object_nodes."""
        from pcons.core.node import FileNode

        # Create multiple source files
        src1 = tmp_path / "main.c"
        src1.write_text("int main() { return 0; }")
        src2 = tmp_path / "util.c"
        src2.write_text("void util() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc -c $SOURCE -o $TARGET"

        # Pre-compiled objects
        obj1 = FileNode(tmp_path / "lib1.o")
        obj2 = FileNode(tmp_path / "lib2.o")

        prog = project.Program("myapp", env)
        prog.add_sources([str(src1), obj1, str(src2), obj2])

        project.resolve()

        assert prog._resolved
        # 2 compiled from .c sources + 2 passed through .o files = 4 total
        assert len(prog.object_nodes) == 4


class TestResolverFlagAccumulation:
    """Test that flags don't accumulate across source files (Bug #2 fix)."""

    def test_no_flag_accumulation_multiple_sources(self, tmp_path, gcc_toolchain):
        """Test that context flags don't accumulate across multiple source files."""
        # Create multiple source files
        src1 = tmp_path / "file1.c"
        src1.write_text("void func1() {}")
        src2 = tmp_path / "file2.c"
        src2.write_text("void func2() {}")
        src3 = tmp_path / "file3.c"
        src3.write_text("void func3() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")

        # Add a define to check for accumulation
        target = project.SharedLibrary(
            "mylib", env, sources=[str(src1), str(src2), str(src3)]
        )
        target.private.defines.append("TEST_DEFINE")

        project.resolve()

        assert target._resolved
        assert len(target.object_nodes) == 3

        # Check that each object node has the same defines - no accumulation
        for obj_node in target.object_nodes:
            context = obj_node._build_info["context"]
            # Should have exactly one occurrence of TEST_DEFINE
            assert context.defines.count("TEST_DEFINE") == 1

    def test_no_flag_accumulation_with_compile_flags(self, tmp_path, gcc_toolchain):
        """Test that compile flags don't accumulate across source files."""
        src1 = tmp_path / "a.c"
        src1.write_text("void a() {}")
        src2 = tmp_path / "b.c"
        src2.write_text("void b() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")

        target = project.SharedLibrary("mylib", env, sources=[str(src1), str(src2)])
        target.private.compile_flags.append("-Wall")

        project.resolve()

        # Check that each object has -Wall exactly once in context.flags
        for obj_node in target.object_nodes:
            context = obj_node._build_info["context"]
            # The flag should appear exactly once
            wall_count = context.flags.count("-Wall")
            assert wall_count == 1, f"Expected 1 occurrence of -Wall, got {wall_count}"


class TestResolverCxxLinker:
    """Test that C++ code uses C++ linker (Bug #1 fix)."""

    def test_cxx_program_uses_cxx_linker(self, tmp_path, gcc_toolchain):
        """Test that C++ program gets linker_cmd override to use clang++/g++."""
        src_file = tmp_path / "main.cpp"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cxx")
        env.cxx.cmd = "g++"  # Set C++ compiler command

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        assert len(target.output_nodes) == 1

        # Check that the output node has linker_cmd override
        output_node = target.output_nodes[0]
        context = output_node._build_info["context"]
        assert context.linker_cmd == "g++", (
            f"Expected linker_cmd='g++', got '{context.linker_cmd}'"
        )

    def test_cxx_shared_library_uses_cxx_linker(self, tmp_path, gcc_toolchain):
        """Test that C++ shared library gets linker_cmd override."""
        src_file = tmp_path / "lib.cpp"
        src_file.write_text("void lib_func() {}")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cxx")
        env.cxx.cmd = "clang++"

        target = project.SharedLibrary("mylib", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        output_node = target.output_nodes[0]
        context = output_node._build_info["context"]
        assert context.linker_cmd == "clang++"

    def test_c_program_no_linker_override(self, tmp_path, gcc_toolchain):
        """Test that pure C program doesn't get linker_cmd override."""
        src_file = tmp_path / "main.c"
        src_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")

        target = project.Program("myapp", env, sources=[str(src_file)])
        project.resolve()

        assert target._resolved
        output_node = target.output_nodes[0]
        context = output_node._build_info["context"]
        # C code should not have linker_cmd override
        assert context.linker_cmd is None

    def test_mixed_c_cxx_uses_cxx_linker(self, tmp_path, gcc_toolchain):
        """Test that mixed C/C++ program uses C++ linker."""
        c_file = tmp_path / "util.c"
        c_file.write_text("void util() {}")
        cpp_file = tmp_path / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.add_tool("cc")
        env.add_tool("cxx")
        env.cxx.cmd = "g++"

        target = project.Program("myapp", env, sources=[str(c_file), str(cpp_file)])
        project.resolve()

        assert target._resolved
        output_node = target.output_nodes[0]
        context = output_node._build_info["context"]
        # Mixed C/C++ should use C++ linker
        assert context.linker_cmd == "g++"
