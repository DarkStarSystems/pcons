# SPDX-License-Identifier: MIT
"""Tests for build context and proper quoting of paths with spaces."""

from __future__ import annotations

from pathlib import Path

from pcons.generators.generator import BaseGenerator
from pcons.toolchains.build_context import CompileLinkContext, MsvcCompileLinkContext


class TestCompileLinkContext:
    """Test CompileLinkContext get_env_overrides."""

    def test_compile_overrides(self) -> None:
        """Verify compile mode returns includes, defines, flags."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            includes=["/usr/include", "/opt/local/include"],
            defines=["DEBUG", "VERSION=1"],
            flags=["-Wall", "-O2"],
            mode="compile",
        )
        overrides = ctx.get_env_overrides()

        # Check that overrides contain the expected keys
        assert "includes" in overrides
        assert "defines" in overrides
        assert "flags" in overrides

        # Includes should be wrapped in ProjectPath
        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 2
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/usr/include"

        # Defines are raw strings
        assert overrides["defines"] == ["DEBUG", "VERSION=1"]

        # Flags are the compile flags (no base flags without _env/_tool_name)
        assert overrides["flags"] == ["-Wall", "-O2"]

    def test_link_overrides(self) -> None:
        """Verify link mode returns libdirs, libs, flags, cmd."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            includes=["/usr/include"],
            defines=["DEBUG"],
            link_flags=["-pthread"],
            libs=["m", "pthread"],
            libdirs=["/usr/lib"],
            linker_cmd="g++",
            mode="link",
        )
        overrides = ctx.get_env_overrides()

        # Link mode should NOT include compile-time keys
        assert "includes" not in overrides
        assert "defines" not in overrides

        # Should include link-time keys
        assert overrides["flags"] == ["-pthread"]
        assert overrides["libs"] == ["m", "pthread"]
        assert overrides["cmd"] == "g++"

        libdirs = cast(list[ProjectPath], overrides["libdirs"])
        assert len(libdirs) == 1
        assert isinstance(libdirs[0], ProjectPath)

    def test_link_overrides_merge_with_env_link_flags(self) -> None:
        """Verify link_flags are merged with env.link.flags, not replaced.

        Regression test: env.link.flags (e.g. -fsanitize=address) must be
        preserved when a target adds public.link_flags.
        """
        import tempfile
        from pathlib import Path

        from pcons.core.project import Project
        from pcons.toolchains.gcc import GccToolchain

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
            toolchain = GccToolchain()
            toolchain._configured = True
            env = project.Environment(toolchain=toolchain)
            env.add_tool("link")
            env.link.flags = ["-fsanitize=address"]

            ctx = CompileLinkContext(
                link_flags=["-pthread"],
                mode="link",
                _env=env,
            )
            overrides = ctx.get_env_overrides()

            # Both env.link.flags and target link_flags must be present
            assert "-fsanitize=address" in overrides["flags"]
            assert "-pthread" in overrides["flags"]

    def test_link_overrides_without_env(self) -> None:
        """Verify link_flags work when no env is provided (no base to merge)."""
        ctx = CompileLinkContext(
            link_flags=["-pthread"],
            mode="link",
        )
        overrides = ctx.get_env_overrides()
        assert overrides["flags"] == ["-pthread"]

    def test_link_overrides_no_duplicate_flags(self) -> None:
        """Verify duplicate flags between env and target are not repeated."""
        import tempfile
        from pathlib import Path

        from pcons.core.project import Project
        from pcons.toolchains.gcc import GccToolchain

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
            toolchain = GccToolchain()
            toolchain._configured = True
            env = project.Environment(toolchain=toolchain)
            env.add_tool("link")
            env.link.flags = ["-fsanitize=address"]

            # Target also has -fsanitize=address — should not appear twice
            ctx = CompileLinkContext(
                link_flags=["-fsanitize=address", "-pthread"],
                mode="link",
                _env=env,
            )
            overrides = ctx.get_env_overrides()
            flags = overrides["flags"]
            assert flags.count("-fsanitize=address") == 1
            assert "-pthread" in flags

    def test_link_overrides_separated_arg_flags_not_split(self) -> None:
        """Verify separated-arg flags like -isystem/-framework aren't split.

        Regression test for the same bug class as the -framework fix (#49):
        naive per-token dedup treats a repeated "-isystem" as already-seen
        and drops it, orphaning the following directory as a bare
        positional argument on the command line.
        """
        import tempfile
        from pathlib import Path

        from pcons.core.project import Project
        from pcons.toolchains.gcc import GccToolchain

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
            toolchain = GccToolchain()
            toolchain._configured = True
            env = project.Environment(toolchain=toolchain)
            env.add_tool("link")
            env.link.flags = ["-isystem", "/usr/local/include", "-framework", "Cocoa"]

            ctx = CompileLinkContext(
                link_flags=["-isystem", "/opt/include", "-framework", "CoreFoundation"],
                mode="link",
                _env=env,
            )
            overrides = ctx.get_env_overrides()

            # Each -isystem/-framework pair must stay intact: the second
            # occurrence of the flag is a different pair (different
            # argument) and must not be dropped or orphaned.
            assert overrides["flags"] == [
                "-isystem",
                "/usr/local/include",
                "-framework",
                "Cocoa",
                "-isystem",
                "/opt/include",
                "-framework",
                "CoreFoundation",
            ]

    def test_toolchain_injected_flag_deduplicated(self) -> None:
        """Toolchain-injected link flags already present are not duplicated."""
        from pcons.tools.requirements import EffectiveRequirements

        class _StubToolchain:
            def get_link_flags_for_target(self, target, output_name, existing_flags):
                # "-Wl,-soname,libfoo.so" duplicates an existing flag; "-Wl,-z" is new.
                return ["-Wl,-soname,libfoo.so", "-Wl,-z"]

        class _StubEnv:
            _toolchain = _StubToolchain()

            def has_tool(self, name: str) -> bool:
                return False

        effective = EffectiveRequirements(link_flags=["-Wl,-soname,libfoo.so"])
        ctx = CompileLinkContext.from_effective_requirements(
            effective,
            mode="link",
            env=_StubEnv(),  # type: ignore[arg-type]
            target=object(),  # type: ignore[arg-type]
            output_name="libfoo.so",
        )

        assert ctx.link_flags.count("-Wl,-soname,libfoo.so") == 1
        assert "-Wl,-z" in ctx.link_flags

    def test_from_effective_requirements_objcxx_uses_cxx_linker(
        self, gcc_toolchain, tmp_project
    ) -> None:
        """.mm sources (link_language "objcxx") must link with the C++ driver.

        Regression test: objcxx has priority 3 (higher than cxx's 2, see
        BaseToolchain.DEFAULT_LANGUAGE_PRIORITY), so a target that mixes
        .mm and .cpp sources picks "objcxx" as its link_language. Before
        this fix, only "cxx" (and "fortran") triggered the C++ linker-driver
        override, so objcxx fell through to the default C linker (gcc) and
        libstdc++/libc++ was never linked in, breaking STL usage in .mm
        files.
        """
        from pcons.core.project import Project
        from pcons.tools.requirements import EffectiveRequirements

        project = Project("test", root_dir=tmp_project, build_dir=tmp_project / "build")
        env = project.Environment(toolchain=gcc_toolchain)

        ctx = CompileLinkContext.from_effective_requirements(
            EffectiveRequirements(),
            mode="link",
            language="objcxx",
            env=env,
        )

        assert ctx.linker_cmd == env.cxx.cmd
        assert ctx.linker_cmd != env.cc.cmd

    def test_from_effective_requirements_cuda_uses_cxx_linker(
        self, gcc_toolchain, tmp_project
    ) -> None:
        """CUDA-linked targets (link_language "cuda") use the host C++ linker.

        CudaToolchain is designed to be used alongside a C/C++ toolchain for
        linking (nvcc only compiles .cu files); when cuda is the
        highest-priority language in a mixed build, the C++ driver must
        still be selected, just as for plain C++.
        """
        from pcons.core.project import Project
        from pcons.tools.requirements import EffectiveRequirements

        project = Project("test", root_dir=tmp_project, build_dir=tmp_project / "build")
        env = project.Environment(toolchain=gcc_toolchain)

        ctx = CompileLinkContext.from_effective_requirements(
            EffectiveRequirements(),
            mode="link",
            language="cuda",
            env=env,
        )

        assert ctx.linker_cmd == env.cxx.cmd
        assert ctx.linker_cmd != env.cc.cmd

    def test_compile_overrides_merge_with_env_flags(self) -> None:
        """Verify compile flags are merged with env.{tool}.flags.

        This tests the compile-side merging that was already correct,
        ensuring it continues to work.
        """
        import tempfile
        from pathlib import Path

        from pcons.core.project import Project
        from pcons.toolchains.gcc import GccToolchain

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
            toolchain = GccToolchain()
            toolchain._configured = True
            env = project.Environment(toolchain=toolchain)
            env.add_tool("cc")
            env.cc.flags = ["-std=c11"]

            ctx = CompileLinkContext(
                flags=["-Wall"],
                mode="compile",
                _tool_name="cc",
                _env=env,
            )
            overrides = ctx.get_env_overrides()

            # Both env.cc.flags and target compile flags must be present
            assert "-std=c11" in overrides["flags"]
            assert "-Wall" in overrides["flags"]

    def test_paths_with_spaces_compile(self) -> None:
        """Verify paths with spaces are preserved in compile mode."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            includes=["/path/with spaces/include", "/another path/headers"],
            mode="compile",
        )
        overrides = ctx.get_env_overrides()

        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 2
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/path/with spaces/include"
        assert includes[1].path == "/another path/headers"

    def test_paths_with_spaces_link(self) -> None:
        """Verify paths with spaces are preserved in link mode."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = CompileLinkContext(
            libdirs=["/lib path/with spaces"],
            mode="link",
        )
        overrides = ctx.get_env_overrides()

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
            mode="compile",
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

    def test_msvc_compile_overrides(self) -> None:
        """Verify MSVC compile mode returns includes, defines."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = MsvcCompileLinkContext(
            includes=["/path/with spaces"],
            defines=["DEBUG"],
            mode="compile",
        )
        overrides = ctx.get_env_overrides()

        includes = cast(list[ProjectPath], overrides["includes"])
        assert len(includes) == 1
        assert isinstance(includes[0], ProjectPath)
        assert includes[0].path == "/path/with spaces"

        assert overrides["defines"] == ["DEBUG"]

    def test_msvc_link_overrides(self) -> None:
        """Verify MSVC link mode adds .lib suffix to libraries."""
        from typing import cast

        from pcons.core.subst import ProjectPath

        ctx = MsvcCompileLinkContext(
            libdirs=["/lib path"],
            libs=["kernel32", "user32.lib"],
            mode="link",
        )
        overrides = ctx.get_env_overrides()

        libdirs = cast(list[ProjectPath], overrides["libdirs"])
        assert len(libdirs) == 1
        assert isinstance(libdirs[0], ProjectPath)

        # MSVC adds .lib suffix if missing
        assert overrides["libs"] == ["kernel32.lib", "user32.lib"]

    def test_msvc_link_overrides_passes_through_non_string_libs(self) -> None:
        """Non-string libs (e.g. PathToken) are passed through unchanged.

        Only plain string lib names get the .lib suffix logic,
        other token types must be forwarded as-is.
        """
        from pcons.core.subst import PathToken

        token = PathToken(prefix="", path="vendor/foo.lib", path_type="project")
        ctx = MsvcCompileLinkContext(
            libs=["kernel32", token],
            mode="link",
        )
        overrides = ctx.get_env_overrides()

        libs = overrides["libs"]
        assert "kernel32.lib" in libs  # string gets .lib suffix
        assert token in libs  # token forwarded unchanged

    def test_msvc_link_overrides_merge_with_env_link_flags(self) -> None:
        """Verify MSVC link_flags are merged with env.link.flags.

        Regression test: same as the Unix variant but for MSVC context.
        """
        import tempfile
        from pathlib import Path

        from pcons.core.project import Project
        from pcons.toolchains.msvc import MsvcToolchain

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
            toolchain = MsvcToolchain()
            toolchain._configured = True
            env = project.Environment(toolchain=toolchain)
            env.add_tool("link")
            env.link.flags = ["/DEBUG"]

            ctx = MsvcCompileLinkContext(
                link_flags=["/LTCG"],
                mode="link",
                _env=env,
            )
            overrides = ctx.get_env_overrides()

            # Both env.link.flags and target link_flags must be present
            assert "/DEBUG" in overrides["flags"]
            assert "/LTCG" in overrides["flags"]


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
        BaseGenerator._generate_pending(project)

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

    def test_ninja_dollar_origin_in_link_flags(self, tmp_path: Path) -> None:
        """Verify $ORIGIN in link flags is properly escaped in ninja output.

        The linker flag -Wl,-rpath,$ORIGIN must survive ninja + shell expansion:
        \\$$ in ninja → \\$ to shell → literal $ to linker.
        """
        from pcons.core.subst import to_shell_command

        # Simulate what happens when a token containing $ORIGIN reaches
        # the ninja generator's shell command formatter
        tokens = ["gcc", "-Wl,-rpath,$ORIGIN", "-o", "$out", "$in"]
        result = to_shell_command(tokens, shell="ninja")

        # $ORIGIN should be escaped for both ninja and shell: \$$
        assert "\\$$ORIGIN" in result
        # Ninja variables should NOT be escaped
        assert " $out " in result or result.endswith("$out")
        assert " $in" in result or result.endswith("$in")


class TestMakefileQuoting:
    """Test that Makefile generator properly quotes values."""

    def test_escape_dollar_for_recipe(self) -> None:
        """Test the _escape_dollar_for_recipe helper directly."""
        from pcons.generators.makefile import MakefileGenerator

        gen = MakefileGenerator()

        # No dollar signs - unchanged
        assert gen._escape_dollar_for_recipe("-Wall -O2") == "-Wall -O2"

        # Dollar signs get escaped for Make, regardless of shell quoting
        assert gen._escape_dollar_for_recipe("-DVAR=$HOME") == "-DVAR=$$HOME"
        assert (
            gen._escape_dollar_for_recipe("'-Wl,-rpath,$ORIGIN/../lib'")
            == "'-Wl,-rpath,$$ORIGIN/../lib'"
        )


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
        env.add_tool("link")

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
        BaseGenerator._generate_pending(project)

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
        BaseGenerator._generate_pending(project)

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
