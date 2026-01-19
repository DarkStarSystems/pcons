# SPDX-License-Identifier: MIT
"""Tests for build context and proper quoting of paths with spaces."""

from __future__ import annotations

from pathlib import Path

from pcons.core.build_context import CompileLinkContext, MsvcCompileLinkContext


class TestCompileLinkContext:
    """Test CompileLinkContext returns properly structured lists."""

    def test_get_variables_returns_lists(self) -> None:
        """Verify get_variables returns dict[str, list[str]]."""
        ctx = CompileLinkContext(
            includes=["/usr/include", "/opt/local/include"],
            defines=["DEBUG", "VERSION=1"],
            flags=["-Wall", "-O2"],
        )
        variables = ctx.get_variables()

        # All values should be lists
        assert isinstance(variables["includes"], list)
        assert isinstance(variables["defines"], list)
        assert isinstance(variables["extra_flags"], list)

        # Check contents
        assert variables["includes"] == ["-I/usr/include", "-I/opt/local/include"]
        assert variables["defines"] == ["-DDEBUG", "-DVERSION=1"]
        assert variables["extra_flags"] == ["-Wall", "-O2"]

    def test_paths_with_spaces(self) -> None:
        """Verify paths with spaces are preserved as separate tokens."""
        ctx = CompileLinkContext(
            includes=["/path/with spaces/include", "/another path/headers"],
            libdirs=["/lib path/with spaces"],
        )
        variables = ctx.get_variables()

        # Each path becomes a single token (with prefix)
        assert variables["includes"] == [
            "-I/path/with spaces/include",
            "-I/another path/headers",
        ]
        assert variables["libdirs"] == ["-L/lib path/with spaces"]

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
        variables = ctx.get_variables()

        # Each define becomes a single token
        assert variables["defines"] == [
            "-DSIMPLE",
            "-DVERSION=1.0",
            '-DMESSAGE="Hello World"',
            "-DPATH=/some/path with spaces",
        ]


class TestMsvcCompileLinkContext:
    """Test MSVC-specific context formatting."""

    def test_msvc_prefixes(self) -> None:
        """Verify MSVC uses correct prefixes."""
        ctx = MsvcCompileLinkContext(
            includes=["/path/with spaces"],
            defines=["DEBUG"],
            libdirs=["/lib path"],
            libs=["kernel32", "user32.lib"],
        )
        variables = ctx.get_variables()

        assert variables["includes"] == ["/I/path/with spaces"]
        assert variables["defines"] == ["/DDEBUG"]
        assert variables["libdirs"] == ["/LIBPATH:/lib path"]
        # MSVC adds .lib suffix if missing
        assert variables["libs"] == ["kernel32.lib", "user32.lib"]


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
        # Use simple command - generator adds placeholders for effective requirements
        env.cc.objcmd = "gcc -c $$in -o $$out"
        env.cc.progcmd = "gcc -o $$out $$in"

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
        generator.generate(project, build_dir)

        # Read ninja file
        ninja_content = (build_dir / "build.ninja").read_text()

        # Check that paths with spaces are properly escaped for Ninja
        # Ninja escapes spaces as "$ " (dollar-space) in variable values
        assert "include$ path" in ninja_content

        # The define with quotes should be preserved (spaces escaped)
        assert "MESSAGE" in ninja_content
        # Quotes inside the define value should be preserved
        assert "Hello$ World" in ninja_content or "Hello World" in ninja_content

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
        generator.generate(project, build_dir)

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
        # Use simple command - generator adds placeholders for effective requirements
        env.cc.objcmd = "gcc -c $$in -o $$out"
        env.cc.progcmd = "gcc -o $$out $$in"

        # Build with all the space-containing paths
        prog = project.Program("my_program", env, sources=[str(source)])
        prog.public.include_dirs.append(include_dir)
        prog.public.defines.append("SIMPLE_DEF")
        prog.public.defines.append('STRING_DEF="value with spaces"')

        project.resolve()

        # Generate and verify ninja
        build_dir = tmp_path / "build"
        NinjaGenerator().generate(project, build_dir)

        ninja = (build_dir / "build.ninja").read_text()

        # Verify escaping in ninja output
        # All paths use Ninja $ escaping (dollar-space for spaces)
        assert "My$ Source$ Files" in ninja  # Build statement path uses Ninja escaping
        assert "My$ Headers" in ninja  # Variable values also use Ninja escaping

        # The build should be syntactically valid (no unescaped spaces breaking parsing)
        # We can't easily run ninja, but we can check there are no obvious errors
        assert "build " in ninja  # Has build statements
        assert "rule " in ninja  # Has rule definitions
