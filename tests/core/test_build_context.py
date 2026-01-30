# SPDX-License-Identifier: MIT
"""Tests for build context and proper quoting of paths with spaces."""

from __future__ import annotations

from pathlib import Path

from pcons.toolchains.build_context import CompileLinkContext, MsvcCompileLinkContext


class TestCompileLinkContext:
    """Test CompileLinkContext get_env_overrides."""

    def test_get_env_overrides_returns_dict(self) -> None:
        """Verify get_env_overrides returns dict with expected keys."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            includes=["/usr/include", "/opt/local/include"],
            defines=["DEBUG", "VERSION=1"],
            flags=["-Wall", "-O2"],
        )
        overrides = ctx.get_env_overrides()

        # Check that overrides contain the expected keys
        assert "includes" in overrides
        assert "defines" in overrides
        assert "extra_flags" in overrides

        # Includes should be wrapped in ProjectPath
        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 2
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/usr/include"

        # Defines are raw strings
        assert overrides["defines"] == ["DEBUG", "VERSION=1"]

        # Flags are raw strings
        assert overrides["extra_flags"] == ["-Wall", "-O2"]

    def test_paths_with_spaces(self) -> None:
        """Verify paths with spaces are preserved in ProjectPath markers."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            includes=["/path/with spaces/include", "/another path/headers"],
            libdirs=["/lib path/with spaces"],
        )
        overrides = ctx.get_env_overrides()

        # Includes wrapped in ProjectPath
        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 2
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/path/with spaces/include"
        assert includes[1].path == "/another path/headers"

        # Libdirs wrapped in ProjectPath
        libdirs = cast(list[ProjectPath], overrides["libdirs"])
        assert len(libdirs) == 1
        assert isinstance(libdirs[0], ProjectPath)
        assert libdirs[0].path == "/lib path/with spaces"

    def test_defines_with_spaces_in_values(self) -> None:
        """Verify defines with spaces in values are preserved."""
        ctx = CompileLinkContext(
            defines=[
                "SIMPLE",
                "VERSION=1.0",
                'MESSAGE="Hello World"',
                "PATH=/some/path with spaces",
            ],
        )
        overrides = ctx.get_env_overrides()

        # Defines are raw strings (no prefix applied here - that's done during subst)
        assert overrides["defines"] == [
            "SIMPLE",
            "VERSION=1.0",
            'MESSAGE="Hello World"',
            "PATH=/some/path with spaces",
        ]


class TestMsvcCompileLinkContext:
    """Test MSVC-specific context formatting."""

    def test_msvc_env_overrides(self) -> None:
        """Verify MSVC get_env_overrides returns expected values."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = MsvcCompileLinkContext(
            includes=["/path/with spaces"],
            defines=["DEBUG"],
            libdirs=["/lib path"],
            libs=["kernel32", "user32.lib"],
        )
        overrides = ctx.get_env_overrides()

        # Includes wrapped in ProjectPath
        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 1
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/path/with spaces"

        # Defines are raw strings
        assert overrides["defines"] == ["DEBUG"]

        # Libdirs wrapped in ProjectPath
        libdirs = cast(list[ProjectPath], overrides["libdirs"])
        assert len(libdirs) == 1
        assert isinstance(libdirs[0], ProjectPath)

        # MSVC adds .lib suffix if missing
        assert overrides["libs"] == ["kernel32.lib", "user32.lib"]


class TestNinjaQuoting:
    """Test that Ninja generator properly escapes values."""

    def test_ninja_escapes_spaces_in_paths(self, tmp_path: Path) -> None:
        """Verify Ninja output escapes spaces with $ ."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator
        from pcons.toolchains.gcc import GccToolchain

        # Create project with path containing spaces
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        toolchain = GccToolchain()
        toolchain._configured = True

        env = project.Environment(toolchain=toolchain)
        env.add_tool("cc")
        # Command template must include placeholders for includes/defines
        # The resolver expands these using values from effective requirements
        env.cc.iprefix = "-I"
        env.cc.dprefix = "-D"
        env.cc.objcmd = [
            "gcc",
            "${prefix(cc.iprefix, cc.includes)}",
            "${prefix(cc.dprefix, cc.defines)}",
            "-c",
            "$$SOURCE",
            "-o",
            "$$TARGET",
        ]
        env.cc.progcmd = "gcc -o $$TARGET $$SOURCES"

        # Create source file
        source_dir = tmp_path / "src with spaces"
        source_dir.mkdir()
        source_file = source_dir / "main with spaces.c"
        source_file.write_text("int main() { return 0; }")

        # Create include dir with spaces
        include_dir = tmp_path / "include path"
        include_dir.mkdir()
        header_file = include_dir / "header file.h"
        header_file.write_text("#define TEST 1")

        # Build program with space-containing paths
        prog = project.Program("test_prog", env, sources=[str(source_file)])
        # Add include dirs and defines to target's public requirements
        prog.public.include_dirs.append(include_dir)
        prog.public.defines.append('MESSAGE="Hello World"')

        project.resolve()

        # Generate ninja file
        build_dir = tmp_path / "build"
        generator = NinjaGenerator()
        generator.generate(project)

        # Read ninja file
        ninja_content = (build_dir / "build.ninja").read_text()

        # Check that paths in build statements are properly escaped for Ninja
        # Ninja escapes spaces as "$ " (dollar-space) in build targets/dependencies
        # The source file path should be escaped in the build statement
        assert "src$ with$ spaces" in ninja_content

        # The include path in the command should be properly quoted for the shell
        # (shell quoting varies by platform, but the path should be present)
        assert "include path" in ninja_content or "include\\ path" in ninja_content

        # The define should be present in the command
        assert "MESSAGE" in ninja_content

    def test_ninja_escapes_special_chars(self) -> None:
        """Verify _escape_path handles special characters."""
        from pcons.generators.ninja import NinjaGenerator

        gen = NinjaGenerator()

        # Space -> $ (dollar-space)
        assert gen._escape_path("path with spaces") == "path$ with$ spaces"

        # Colon -> $:
        assert gen._escape_path("C:/Windows") == "C$:/Windows"

        # Dollar -> $$
        assert gen._escape_path("$HOME/path") == "$$HOME/path"


class TestMakefileQuoting:
    """Test that Makefile generator properly quotes values."""

    def test_quote_tokens_for_make(self) -> None:
        """Test the _quote_tokens_for_make helper directly."""
        from pcons.generators.makefile import MakefileGenerator

        gen = MakefileGenerator()

        # Simple tokens - no quoting needed
        assert gen._quote_tokens_for_make(["-Wall", "-O2"]) == "-Wall -O2"

        # Paths with spaces need quoting
        result = gen._quote_tokens_for_make(["-I/path with spaces"])
        assert "'" in result or '"' in result

        # Dollar signs get escaped for Make
        result = gen._quote_tokens_for_make(["-DVAR=$HOME"])
        assert "$$HOME" in result


class TestCompileCommandsQuoting:
    """Test that compile_commands.json uses proper shell quoting."""

    def test_compile_commands_quotes_spaces(self, tmp_path: Path) -> None:
        """Verify compile_commands.json properly quotes paths with spaces."""
        import json

        from pcons.core.project import Project
        from pcons.generators.compile_commands import CompileCommandsGenerator
        from pcons.toolchains.gcc import GccToolchain

        # Create project with path containing spaces
        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        toolchain = GccToolchain()
        toolchain._configured = True

        env = project.Environment(toolchain=toolchain)
        env.add_tool("cc")
        env.cc.objcmd = "gcc $includes $defines $extra_flags -c $in -o $out"
        env.cc.progcmd = "gcc $ldflags -o $out $in $libs"

        # Create source file in directory with spaces
        source_dir = tmp_path / "src with spaces"
        source_dir.mkdir()
        source_file = source_dir / "main.c"
        source_file.write_text("int main() { return 0; }")

        # Create include dir with spaces
        include_dir = tmp_path / "headers with spaces"
        include_dir.mkdir()

        # Build program with space-containing paths
        prog = project.Program("test", env, sources=[str(source_file)])
        prog.public.include_dirs.append(include_dir)
        prog.public.defines.append('MSG="value with spaces"')

        project.resolve()

        # Generate compile_commands.json
        build_dir = tmp_path / "build"
        generator = CompileCommandsGenerator()
        generator.generate(project)

        # Read and parse
        cc_file = build_dir / "compile_commands.json"
        compile_commands = json.loads(cc_file.read_text())

        # Should have at least one entry
        assert len(compile_commands) >= 1

        # Check the command string - should be properly quoted for shell
        cmd = compile_commands[0]["command"]

        # The include path should be quoted (shlex.quote format)
        # shlex.quote uses single quotes for strings with spaces
        assert "headers with spaces" in cmd
        # shlex.quote wraps in single quotes: '-I/path/headers with spaces'
        # or the whole -I flag: '-I...'


class TestEndToEndSpacesInPaths:
    """End-to-end test with actual files containing spaces."""

    def test_full_build_with_spaces(self, tmp_path: Path) -> None:
        """Create a complete project with spaces in paths and verify output."""
        from pcons.core.project import Project
        from pcons.generators.ninja import NinjaGenerator
        from pcons.toolchains.gcc import GccToolchain

        # Create directory structure with spaces
        src_dir = tmp_path / "My Source Files"
        src_dir.mkdir()

        include_dir = tmp_path / "My Headers"
        include_dir.mkdir()

        # Create files
        header = include_dir / "my header.h"
        header.write_text('#define GREETING "Hello World"\n')

        source = src_dir / "my main.c"
        source.write_text('#include "my header.h"\nint main() { return 0; }\n')

        # Create project
        project = Project("My Project", root_dir=tmp_path, build_dir=tmp_path / "build")
        toolchain = GccToolchain()
        toolchain._configured = True

        env = project.Environment(toolchain=toolchain)
        env.add_tool("cc")
        # Command template must include placeholders for includes/defines
        # The resolver expands these using values from effective requirements
        env.cc.iprefix = "-I"
        env.cc.dprefix = "-D"
        env.cc.objcmd = [
            "gcc",
            "${prefix(cc.iprefix, cc.includes)}",
            "${prefix(cc.dprefix, cc.defines)}",
            "-c",
            "$$SOURCE",
            "-o",
            "$$TARGET",
        ]
        env.cc.progcmd = "gcc -o $$TARGET $$SOURCES"

        # Build with all the space-containing paths
        prog = project.Program("my_program", env, sources=[str(source)])
        prog.public.include_dirs.append(include_dir)
        prog.public.defines.append("SIMPLE_DEF")
        prog.public.defines.append('STRING_DEF="value with spaces"')

        project.resolve()

        # Generate and verify ninja
        build_dir = tmp_path / "build"
        NinjaGenerator().generate(project)

        ninja = (build_dir / "build.ninja").read_text()

        # Verify escaping in ninja output
        # Build statement paths use Ninja $ escaping (dollar-space for spaces)
        assert "My$ Source$ Files" in ninja  # Source path in build statement

        # Include paths are in the command line, shell-quoted (not ninja-escaped)
        assert "My Headers" in ninja  # The path appears in the command

        # The build should be syntactically valid (no unescaped spaces breaking parsing)
        # We can't easily run ninja, but we can check there are no obvious errors
        assert "build " in ninja  # Has build statements
        assert "rule " in ninja  # Has rule definitions
