# SPDX-License-Identifier: MIT
"""Tests for user error experience in pcons.

Tests ~50 plausible user mistakes and verifies pcons gives helpful,
actionable error messages. Tests marked with xfail indicate known gaps
in error handling where pcons currently produces unhelpful errors or
silently accepts invalid input.

These serve as both regression tests for existing good errors and a
roadmap for future error handling improvements.
"""

import functools
import inspect
import textwrap
import warnings
from pathlib import Path

import pytest

from pcons.core.errors import (
    BuilderError,
    DependencyCycleError,
    MissingSourceError,
    MissingVariableError,
    PconsError,
)
from pcons.core.invocation import RUN_NAME
from pcons.core.project import Project
from pcons.core.subst import PathToken
from pcons.util.pyaction import run


@pytest.fixture
def project_env(tmp_path, gcc_toolchain):
    """Project + Environment ready for target creation."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.c").write_text("int main() { return 0; }\n")
    (tmp_path / "src" / "lib.c").write_text("int lib_func() { return 1; }\n")
    project = Project("test", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    return project, env


# =============================================================================
# Category 1: Wrong Argument Types
# =============================================================================


class TestWrongArgumentTypes:
    """Users passing wrong types to builders and methods."""

    def test_program_sources_string_not_list(self, project_env):
        """User passes a string instead of a list for sources.

        This is probably the #1 beginner mistake. The string "src/main.c" is
        iterable, so it gets iterated character by character: "s", "r", "c", ...
        """
        project, env = project_env
        with pytest.raises((TypeError, ValueError), match="sources"):
            project.Program("app", env, sources="src/main.c")

    def test_program_sources_bare_path_not_list(self, project_env):
        """User passes a bare Path instead of [Path]."""
        project, env = project_env
        with pytest.raises((TypeError, ValueError), match="sources"):
            project.Program("app", env, sources=Path("src/main.c"))

    def test_program_name_not_string(self, project_env):
        """User passes an int as the target name."""
        project, env = project_env
        with pytest.raises(TypeError, match="name"):
            project.Program(123, env, sources=["src/main.c"])

    def test_link_accepts_string_library(self, project_env):
        """A string library name is a valid link() argument (raw link token)."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            app.link("mylib")
        assert "mylib" in app.public.link_libs

    def test_link_non_target_non_string(self, project_env):
        """A non-Target, non-str argument to link() is a TypeError."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(TypeError):
            app.link(42)

    def test_link_list_instead_of_varargs(self, project_env):
        """User passes a list instead of unpacking: link([a, b]) vs link(a, b)."""
        project, env = project_env
        lib = project.StaticLibrary("mylib", env, sources=["src/lib.c"])
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(TypeError):
            app.link([lib])

    def test_add_sources_string_not_list(self, project_env):
        """User passes a string to add_sources instead of a list."""
        project, env = project_env
        app = project.Program("app", env)
        with pytest.raises((TypeError, ValueError), match="sources"):
            app.add_sources("src/main.c")

    def test_environment_toolchain_unknown_string(self, project_env):
        """A toolchain name that isn't registered fails with the known names."""
        project, _ = project_env
        with pytest.raises(ValueError, match="Unknown toolchain 'gcc-13'.*gcc"):
            project.Environment(toolchain="gcc-13")

    def test_environment_toolchain_wrong_type(self, project_env):
        """A non-string, non-Toolchain object fails with a clear message."""
        project, _ = project_env
        with pytest.raises(TypeError, match="Toolchain object or a registered"):
            project.Environment(toolchain=42)

    def test_public_include_dirs_assigned_string(self, project_env):
        """User assigns a string instead of appending to the list.

        target.public.include_dirs = "/usr/include"  # wrong
        target.public.include_dirs.append("/usr/include")  # right
        """
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        # Assigning a string to a usage requirement raises immediately
        with pytest.raises(TypeError, match="list"):
            app.public.include_dirs = "/usr/include"

    def test_depends_wrong_type(self, project_env):
        """User passes a nonsense type to depends() -- pcons catches this."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises((TypeError, AttributeError)):
            app.depends(42)


# =============================================================================
# Category 2: Missing or Swapped Arguments
# =============================================================================


class TestMissingSwappedArguments:
    """Users forgetting required arguments or putting them in wrong order."""

    def test_program_missing_env(self, project_env):
        """User forgets the env argument entirely."""
        project, env = project_env
        with pytest.raises(TypeError):
            project.Program("app", sources=["src/main.c"])

    def test_program_env_in_name_position(self, project_env):
        """User passes env where name should be.

        project.Program(env, sources=[...])  # wrong
        project.Program("app", env, sources=[...])  # right
        """
        project, env = project_env
        # env goes to name position, so Python complains about missing env arg
        with pytest.raises(TypeError):
            project.Program(env, sources=["src/main.c"])

    def test_project_no_name(self):
        """User forgets to name the project."""
        with pytest.raises(TypeError):
            Project()

    def test_install_no_sources(self, project_env):
        """User forgets sources for Install."""
        project, env = project_env
        with pytest.raises(TypeError):
            project.Install("dist")


# =============================================================================
# Category 3: Typos and Misspellings
# =============================================================================


class TestTyposAndMisspellings:
    """Users misspelling method/attribute names."""

    def test_typo_builder_name(self, project_env):
        """User misspells StaticLibrary."""
        project, env = project_env
        with pytest.raises(AttributeError):
            project.StaticLibary("mylib", env, sources=["src/lib.c"])

    def test_typo_env_tool_name(self, project_env):
        """User misspells a tool namespace like 'ccx' instead of 'cxx'."""
        _, env = project_env
        with pytest.raises(AttributeError, match="Tool"):
            _ = env.ccx

    def test_typo_usage_requirement_name_raises(self, project_env):
        """A typo in a usage-requirement name is an error.

        Accepting unknown names is tempting -- UsageRequirements has to stay
        open-ended for toolchains, and the flags a typo drops are supposedly
        visible in the build output. They aren't: `private.lib_dirs.append(...)`
        looks like it worked, and the build fails with `ld: library
        'OpenImageIO' not found` -- naming the library rather than the typo,
        several steps from the cause.

        Extensibility is explicit instead: a toolchain that consumes its own
        name calls register_usage_requirement().
        """
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])

        with pytest.raises(AttributeError, match="Did you mean 'include_dirs'"):
            app.public.includedirs.append("/usr/include")

    def test_typo_public_called_as_method(self, project_env):
        """User calls target.public() instead of accessing target.public."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(TypeError):
            app.public()

    def test_scons_style_library_name(self, project_env):
        """User tries SCons-style 'Library' instead of 'StaticLibrary'."""
        project, env = project_env
        with pytest.raises(AttributeError):
            project.Library("mylib", env, sources=["src/lib.c"])


# =============================================================================
# Category 4: Wrong API Usage Order
# =============================================================================


class TestWrongApiOrder:
    """Users calling API methods in the wrong order."""

    def test_generate_with_no_targets(self, project_env):
        """User calls generate() without defining any targets.

        Currently silently generates an empty build file. At minimum
        this should log a warning.
        """
        from pcons.generators.generator import BaseGenerator

        project, env = project_env
        # This succeeds but produces a useless empty build file
        project.generate()
        BaseGenerator._generate_pending(project)
        # Verify it didn't crash -- the question is whether it SHOULD warn
        assert project._resolved

    def test_resolve_twice_is_safe(self, project_env):
        """Calling resolve() twice should be safe (idempotent or warn)."""
        project, env = project_env
        project.Program("app", env, sources=["src/main.c"])
        project.resolve()
        # Second resolve should not crash
        project.resolve()

    def test_add_sources_after_resolve(self, project_env):
        """User adds sources after calling resolve().

        The sources are silently ignored because resolve has already run.
        """
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        project.resolve()
        # Adding sources after resolve should warn or raise
        with pytest.raises((PconsError, RuntimeError), match="resolve"):
            app.add_sources(["src/lib.c"])

    def test_link_after_resolve(self, project_env):
        """User links a library after resolve has already run."""
        project, env = project_env
        lib = project.StaticLibrary("mylib", env, sources=["src/lib.c"])
        app = project.Program("app", env, sources=["src/main.c"])
        project.resolve()
        # Linking after resolve should warn or raise
        with pytest.raises((PconsError, RuntimeError), match="resolve"):
            app.public.link_libs.append(lib)

    def test_modify_flags_before_resolve_is_ok(self, project_env):
        """Modifying flags after target creation but before resolve is valid.

        This documents that flags are evaluated lazily at resolve time.
        """
        project, env = project_env
        project.Program("app", env, sources=["src/main.c"])
        # This is fine -- flags aren't evaluated until resolve
        env.cc.flags.append("-Wall")
        project.resolve()
        # Should not crash


# =============================================================================
# Category 5: Path Errors
# =============================================================================


class TestPathErrors:
    """Users making path-related mistakes."""

    def test_nonexistent_source_file(self, project_env):
        """User references a source file that doesn't exist."""
        project, env = project_env
        project.Program("app", env, sources=["src/missing.c"])
        errors = project.validate()
        # Should have at least one MissingSourceError
        assert any(isinstance(e, MissingSourceError) for e in errors)

    def test_nonexistent_source_strict(self, project_env):
        """Strict resolve should raise on missing sources."""
        project, env = project_env
        project.Program("app", env, sources=["src/missing.c"])
        with pytest.raises(PconsError):
            project.resolve(strict=True)

    def test_nonexistent_source_error_message_quality(self, project_env):
        """Missing source error should include the path and a helpful hint."""
        project, env = project_env
        project.Program("app", env, sources=["src/missing.c"])
        errors = project.validate()
        missing_errors = [e for e in errors if isinstance(e, MissingSourceError)]
        assert len(missing_errors) >= 1
        msg = str(missing_errors[0])
        assert "missing.c" in msg
        # Should mention it's relative, suggest checking the path
        assert "relative" in msg.lower() or "source" in msg.lower()

    @pytest.mark.xfail(
        reason="Backslash paths on Unix not normalized or warned about", strict=True
    )
    def test_source_with_backslashes_on_unix(self, project_env):
        """User uses backslash paths (common when copying from Windows docs)."""
        import sys

        if sys.platform == "win32":
            pytest.skip("backslashes are valid on Windows")
        project, env = project_env
        # Should normalize or warn about backslashes
        with pytest.raises((ValueError, PconsError), match="backslash|separator"):
            project.Program("app", env, sources=["src\\main.c"])


# =============================================================================
# Category 6: Dependency Mistakes
# =============================================================================


class TestDependencyMistakes:
    """Users making dependency-related errors."""

    def test_circular_dependency(self, project_env):
        """Two shared libraries linking each other. (Static libraries may.)"""
        project, env = project_env
        lib_a = project.SharedLibrary("liba", env, sources=["src/lib.c"])
        lib_b = project.SharedLibrary("libb", env, sources=["src/main.c"])
        lib_a.public.link_libs.append(lib_b)
        lib_b.public.link_libs.append(lib_a)
        errors = project.validate()
        assert any(isinstance(e, DependencyCycleError) for e in errors)

    def test_circular_dependency_raises_on_resolve(self, project_env):
        """A cycle stops the run whatever `strict` says: it has no build
        order, so nothing downstream of it could be built even if the build
        files were written."""
        project, env = project_env
        lib_a = project.SharedLibrary("liba", env, sources=["src/lib.c"])
        lib_b = project.SharedLibrary("libb", env, sources=["src/main.c"])
        lib_a.public.link_libs.append(lib_b)
        lib_b.public.link_libs.append(lib_a)
        with pytest.raises(DependencyCycleError):
            project.resolve()

    def test_self_link(self, project_env):
        """Target links itself."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises((DependencyCycleError, ValueError), match="self|cycle"):
            app.private.link_libs.append(app)

    def test_depends_on_self(self, project_env):
        """Target depends on itself."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises((DependencyCycleError, ValueError), match="self|cycle"):
            app.depends(app)

    def test_duplicate_link_is_safe(self, project_env):
        """Linking the same library twice should be deduplicated, not error."""
        project, env = project_env
        lib = project.StaticLibrary("mylib", env, sources=["src/lib.c"])
        app = project.Program("app", env, sources=["src/main.c"])
        app.private.link_libs.append(lib)
        app.private.link_libs.append(lib)
        # Should not crash and lib should appear only once
        assert app.dependencies.count(lib) == 1

    def test_duplicate_source_raises(self, project_env):
        """A source listed twice must be rejected, not emitted twice.

        Object nodes are shared, so it compiles once and is consumed twice, and
        the linker reports duplicate symbols naming a single object file --
        which reads like a linker bug, not a build-description mistake.
        """
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(ValueError, match="already has source"):
            app.add_sources(["src/main.c"])

    def test_duplicate_source_within_one_call_raises(self, project_env):
        project, env = project_env
        with pytest.raises(ValueError, match="already has source"):
            project.Program("app", env, sources=["src/main.c", "src/main.c"])

    def test_duplicate_source_target_raises(self, project_env):
        project, env = project_env
        gen = env.Command(
            target="gen.c", source="gen.in", command=["cp", "$SOURCE", "$TARGET"]
        )
        app = project.Program("app", env, sources=["src/main.c"])
        app.add_source(gen)
        with pytest.raises(ValueError, match="already has source target"):
            app.add_source(gen)

    def test_link_not_deprecated(self, project_env):
        """link() is a first-class API and must not emit any warning."""
        project, env = project_env
        lib = project.StaticLibrary("mylib", env, sources=["src/lib.c"])
        app = project.Program("app", env, sources=["src/main.c"])
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            app.link(lib)
        assert lib in app.public.link_libs


# =============================================================================
# Category 7: Variable and Flag Errors
# =============================================================================


class TestVariableAndFlagErrors:
    """Users making mistakes with variables and flags."""

    def test_flags_string_instead_of_list(self, project_env):
        """User assigns a string to flags instead of a list.

        env.cc.flags = "-Wall -O2"  # wrong, treated as single flag or iterated
        env.cc.flags = ["-Wall", "-O2"]  # right
        """
        _, env = project_env
        with pytest.raises(TypeError, match="list"):
            env.cc.flags = "-Wall -O2"

    def test_undefined_variable_in_command(self, project_env):
        """User references an undefined variable in a command template."""
        project, env = project_env
        with pytest.raises(MissingVariableError, match="NONEXISTENT_TOOL"):
            project.Command(
                "gen",
                env,
                target="out.txt",
                source="src/main.c",
                command="$NONEXISTENT_TOOL $SOURCE -o $TARGET",
            )

    def test_undefined_variable_message_quality(self):
        """MissingVariableError should include helpful hints."""
        err = MissingVariableError("ORIGIN")
        msg = str(err)
        # Should suggest $$ escaping for bare variables
        assert "$$ORIGIN" in msg
        assert "literally" in msg

    def test_undefined_dotted_variable_no_escape_hint(self):
        """Dotted variables like $tool.var should NOT suggest $$ escaping."""
        err = MissingVariableError("link.badvar")
        msg = str(err)
        assert "$$" not in msg

    @pytest.mark.xfail(reason="No validation of -I prefix in include_dirs", strict=True)
    def test_include_dir_with_flag_prefix(self, project_env):
        """User includes the -I prefix in include_dirs (generates -I-I/path)."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises((ValueError, PconsError), match="-I"):
            app.public.include_dirs.append("-I/usr/include")
            # Even if we can't prevent append, detect at resolve
            project.resolve()


# =============================================================================
# Category 8: Environment Misuse
# =============================================================================


class TestEnvironmentMisuse:
    """Users misusing Environment objects."""

    def test_no_toolchain_env_for_program(self, project_env):
        """Using an environment without a toolchain for compilation.

        Currently this only logs warnings about missing tools. It should
        give a clear error telling the user they need a toolchain.
        """
        project, _ = project_env
        bare_env = project.Environment()  # No toolchain
        project.Program("app", bare_env, sources=["src/main.c"])
        # Should fail at resolve with a helpful message
        with pytest.raises(PconsError, match="toolchain"):
            project.resolve()

    def test_clone_independence(self, project_env):
        """Verify that cloned environments are independent."""
        _, env = project_env
        clone = env.clone()
        clone.cc.flags.append("-DCLONE_ONLY")
        # Original should NOT have the flag
        assert "-DCLONE_ONLY" not in env.cc.flags

    def test_set_variant_invalid_name(self, project_env):
        """User passes a nonexistent variant name."""
        _, env = project_env
        with pytest.raises((ValueError, PconsError), match="variant"):
            env.set_variant("nonexistent_variant_name")


# =============================================================================
# Category 9: Target Naming Issues
# =============================================================================


class TestTargetNaming:
    """Users making mistakes with target names."""

    def test_duplicate_target_name_gives_good_error(self, project_env):
        """Duplicate target names should give a clear error with locations."""
        project, env = project_env
        project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(ValueError, match="already exists"):
            project.Program("app", env, sources=["src/lib.c"])

    def test_target_name_with_spaces(self, project_env):
        """Target names with spaces may break ninja output."""
        project, env = project_env
        with pytest.raises((ValueError, PconsError), match="name"):
            project.Program("my app", env, sources=["src/main.c"])

    def test_empty_target_name(self, project_env):
        """Empty target name should be rejected."""
        project, env = project_env
        with pytest.raises((ValueError, PconsError), match="name"):
            project.Program("", env, sources=["src/main.c"])

    def test_target_name_with_slashes_is_ok(self, project_env):
        """Target names with slashes are valid (used by archive/install builders)."""
        project, env = project_env
        # Slashes are allowed -- used for subdirectory-style target names
        app = project.Program("bin/app", env, sources=["src/main.c"])
        assert app.name == "bin/app"

    def test_target_name_special_chars(self, project_env):
        """Target names with special chars may break ninja."""
        project, env = project_env
        with pytest.raises((ValueError, PconsError), match="name"):
            project.Program("app@v2!", env, sources=["src/main.c"])


# =============================================================================
# Category 10: Builder Edge Cases
# =============================================================================


class TestBuilderEdgeCases:
    """Edge cases in builder usage."""

    def test_install_as_with_list_source(self, project_env):
        """InstallAs with a list source should give a clear error."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(BuilderError, match="single|Install\\(\\)"):
            project.InstallAs("dist/app", [app])

    def test_program_empty_sources_list_is_valid(self, project_env):
        """Program with empty sources=[] is valid -- sources can be added later.

        Users may create a target first and add sources afterward via
        add_sources(), so an empty initial list is intentionally allowed.
        """
        project, env = project_env
        app = project.Program("app", env, sources=[])
        app.add_sources(["src/main.c"])
        assert len(app.sources) == 1

    def test_install_accepts_target_as_source(self, project_env):
        """Install should accept Target objects (resolved to outputs later)."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        # This should work -- Install resolves Target to output_nodes
        install = project.Install("dist", [app])
        assert install is not None


PYACTION_DEFAULT = "a value the build script computed"


def build_script_function(tmp_path, source, name="render"):
    """Define a function the way a build script does, under ``__pcons__``.

    The module name is half of what PyAction's messages reason about: a
    helper defined here has nowhere to be imported from, and a test module,
    which is importable, cannot stand in for that.
    """
    path = tmp_path / "pcons-build.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    namespace = {"__name__": RUN_NAME, "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    return namespace[name]


SCRIPT_VALUE = "a value the build script computed"


class TestEveryPyActionRemedyWorks:
    """Every remedy above, typed out and run.

    A remedy nobody has followed is a remedy nobody has checked. Each test
    here types the sentence the matching refusal prints and asserts the
    result is a real edge. Where the remedy moves a value across the
    configure/build boundary, the function is then run through the build-time
    runner, so the value is proven to arrive rather than merely to configure.
    """

    def edge_sources(self, target):
        """The edge's own sources, as node paths."""
        info = target.output_nodes[0]._build_info
        return [node.path.as_posix() for node in info["sources"]]

    def run_edge(self, project, action, tmp_path, **call):
        """Make the edge, then run its function the way the runner will."""
        made = action(**call)
        project.resolve()
        module, args = (
            tmp_path / Path(token.path)
            for token in made.output_nodes[0]._build_info["command"]
            if isinstance(token, PathToken)
        )
        out = tmp_path / "written.txt"
        run(str(module), str(args), [str(out)], [str(tmp_path / "src" / "main.c")])
        return made, out

    def test_a_partial_becomes_an_argument_of_the_call(self, project_env, tmp_path):
        """ "give its bound arguments to the call: action(target=..., bound=value)"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, n):
            from pathlib import Path

            Path(targets[0]).write_text(str(n), encoding="utf-8")

        _, out = self.run_edge(project, render, tmp_path, target="out.txt", n=1)

        assert out.read_text(encoding="utf-8") == "1"

    def test_a_lambda_becomes_a_def(self, project_env):
        """ "write it as a def"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        assert render(target="out.txt").name == "out"

    def test_a_method_moves_out_of_the_class(self, project_env):
        """ "move the def out of the class"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        assert render(target="out.txt").name == "out"

    def test_a_coroutine_becomes_a_plain_def(self, project_env):
        """ "Write it as a plain def"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        assert render(target="out.txt").name == "out"

    def test_a_closure_becomes_a_parameter(self, project_env, tmp_path):
        """ "Take it as a parameter and pass it at the call: f(target=..., title=...)"."""
        project, env = project_env
        title = "from the enclosing scope"

        def make():
            @env.PyAction()
            def render(sources, targets, title):
                from pathlib import Path

                Path(targets[0]).write_text(title, encoding="utf-8")

            return render

        _, out = self.run_edge(project, make(), tmp_path, target="out.txt", title=title)

        assert out.read_text(encoding="utf-8") == title

    def test_an_import_moves_into_the_body(self, project_env, tmp_path):
        """ "Import Path inside the function body, the way this script imports it"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets):
            from pathlib import Path

            Path(targets[0]).write_text(Path(sources[0]).name, encoding="utf-8")

        _, out = self.run_edge(project, render, tmp_path, target="out.txt")

        assert out.read_text(encoding="utf-8") == "main.c"

    def test_a_module_import_moves_into_the_body(self, project_env, tmp_path):
        """'Write "import json" at the top of the function body'."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets):
            import json
            from pathlib import Path

            Path(targets[0]).write_text(json.dumps({"ok": True}), encoding="utf-8")

        _, out = self.run_edge(project, render, tmp_path, target="out.txt")

        assert out.read_text(encoding="utf-8") == '{"ok": true}'

    def test_a_default_becomes_a_parameter_passed_at_the_call(
        self, project_env, tmp_path
    ):
        """ "write the parameter without a default and pass it at the call"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, label):
            from pathlib import Path

            Path(targets[0]).write_text(label, encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", label=SCRIPT_VALUE
        )

        assert out.read_text(encoding="utf-8") == SCRIPT_VALUE

    def test_a_script_value_becomes_a_parameter(self, project_env, tmp_path):
        """ "Take SRC_DIR as a parameter and pass it at the call"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, SRC_DIR):  # noqa: N803
            from pathlib import Path

            Path(targets[0]).write_text(SRC_DIR, encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", SRC_DIR="src"
        )

        assert out.read_text(encoding="utf-8") == "src"

    def test_a_script_local_helper_is_written_out_in_the_body(
        self, project_env, tmp_path
    ):
        """ "write out what it does inside the function body"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets):
            from pathlib import Path

            def helper(value):
                return value.upper()

            Path(targets[0]).write_text(helper("ok"), encoding="utf-8")

        _, out = self.run_edge(project, render, tmp_path, target="out.txt")

        assert out.read_text(encoding="utf-8") == "OK"

    def test_dunder_file_becomes_a_path_passed_at_the_call(self, project_env, tmp_path):
        """'Take the path it means as a parameter: f(target=..., here=...)'."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, here):
            from pathlib import Path

            Path(targets[0]).write_text(Path(here).name, encoding="utf-8")

        _, out = self.run_edge(
            project,
            render,
            tmp_path,
            target="out.txt",
            here=str(project.root_dir / "build.py"),
        )

        assert out.read_text(encoding="utf-8") == "build.py"

    def test_a_reserved_parameter_is_renamed(self, project_env, tmp_path):
        """ "Rename it in the def and at the call"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, input_file):
            from pathlib import Path

            Path(targets[0]).write_text(input_file, encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", input_file="renamed"
        )

        assert out.read_text(encoding="utf-8") == "renamed"

    def test_the_signature_the_message_printed_is_callable(self, project_env):
        """The bind message prints the signature; typing it works."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        assert render(target="out.txt", title="typed from the message").name == "out"

    def test_a_target_moves_to_source(self, project_env):
        """ "List it in source= instead, and the function receives its output paths"."""
        project, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        @env.PyAction()
        def render(sources, targets):
            return sources

        edge = render(target="out.txt", source=[made])
        project.resolve()

        assert self.edge_sources(edge) == ["build/made.txt"]

    def test_a_node_moves_to_source(self, project_env):
        """The same remedy, for a node."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return sources

        edge = render(target="out.txt", source=[project.node("src/main.c")])
        project.resolve()

        assert self.edge_sources(edge) == ["src/main.c"]

    def test_the_environment_becomes_a_value_read_here(self, project_env, tmp_path):
        """ "Read what the function needs from it here, and pass that"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, env_name):
            from pathlib import Path

            Path(targets[0]).write_text(env_name, encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", env_name=str(env.name)
        )

        assert out.read_text(encoding="utf-8") == str(env.name)

    def test_a_tool_namespace_becomes_its_values(self, project_env, tmp_path):
        """ "env.cc.flags rather than env.cc"."""
        project, env = project_env
        env.cc.flags = ["-O2"]

        @env.PyAction()
        def render(sources, targets, flags):
            from pathlib import Path

            Path(targets[0]).write_text(" ".join(flags), encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", flags=list(env.cc.flags)
        )

        assert out.read_text(encoding="utf-8") == "-O2"

    def test_an_unpicklable_argument_becomes_a_path(self, project_env, tmp_path):
        """ "Pass what describes it instead, a path or a string"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, path):
            from pathlib import Path

            Path(targets[0]).write_text(Path(path).read_text(encoding="utf-8"))

        _, out = self.run_edge(
            project,
            render,
            tmp_path,
            target="out.txt",
            path=str(tmp_path / "src" / "main.c"),
        )

        assert out.read_text(encoding="utf-8").startswith("int main()")

    def test_two_functions_of_one_name_are_renamed(self, project_env, tmp_path):
        """ "Rename one of the functions"."""
        _, env = project_env
        for sub in ("one", "two"):
            (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        first = build_script_function(
            tmp_path / "one",
            """
            def render(sources, targets):
                return 1
            """,
        )
        second = build_script_function(
            tmp_path / "two",
            """
            def render_more(sources, targets):
                return 2
            """,
            name="render_more",
        )

        env.PyAction()(first)(target="a.txt")
        env.PyAction()(second)(target="b.txt")

        assert (tmp_path / "build/pyact/render.py").is_file()
        assert (tmp_path / "build/pyact/render_more.py").is_file()

    def test_one_decoration_called_twice(self, project_env, tmp_path):
        """ "Decorate the function once and call the action twice"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        render(target="a.txt")
        render(target="b.txt")

        generated = sorted(
            q.name
            for q in (tmp_path / "build" / "pyact").iterdir()
            if q.suffix in (".py", ".pkl")
        )

        assert generated == ["a.args.pkl", "b.args.pkl", "render.py"]

    def test_one_of_the_edges_is_named(self, project_env, tmp_path):
        """'Name one of the edges, name="something-else"'."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        render(target="report.txt")
        second = render(target="sub/report.txt", name="sub-report")

        assert second.name == "sub-report"
        assert (tmp_path / "build/pyact/sub_report.args.pkl").is_file()

    def test_one_environment_gets_a_build_prefix(self, project_env, tmp_path):
        """ "Give one environment its own build_prefix"."""
        project, _ = project_env
        env = project.Environment(name="host")
        other = project.Environment(name="other")
        other.build_prefix = "other"

        def decorate(environment):
            @environment.PyAction()
            def render(sources, targets):
                return 1

            return render

        decorate(env)(target="report.txt")
        decorate(other)(target="report.txt")

        assert (tmp_path / "build/pyact/render.py").is_file()
        assert (tmp_path / "build/other/pyact/render.py").is_file()

    def test_the_first_two_parameters_become_positional(self, project_env):
        """ "write the first two as plain parameters: def f(sources, targets, ...)"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        assert render(target="out.txt", title="t").name == "out"

    def test_the_slash_moves_up_to_follow_targets(self, project_env, tmp_path):
        """ "Move the / up so it follows targets: def f(sources, targets, /, title)"."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, /, title):
            from pathlib import Path

            Path(targets[0]).write_text(title, encoding="utf-8")

        _, out = self.run_edge(
            project, render, tmp_path, target="out.txt", title="after the slash"
        )

        assert out.read_text(encoding="utf-8") == "after the slash"

    def test_sources_at_the_call_becomes_source(self, project_env):
        """ "The edge's own files are spelled source="."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        edge = render(target="out.txt", source=["src/main.c"], title="t")
        project.resolve()

        assert self.edge_sources(edge) == ["src/main.c"]


class TestPyActionErrors:
    """What env.PyAction() says when a function cannot travel to build time.

    Every message is read here as the user reads it, whole, because the
    feature's failures are all configure-time refusals whose only job is to
    say what to type instead.
    """

    def test_a_lambda_says_to_write_a_def(self, project_env):
        """No name is invented for it: '<lambda>' twice in one sentence."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:
            env.PyAction()(lambda sources, targets: None)

        message = str(caught.value)
        assert "PyAction was given a lambda." in message
        assert "write it as a def" in message
        assert "<lambda>" not in message

    def test_a_closure_names_the_variable_and_spells_the_call(self, project_env):
        _, env = project_env
        title = "report"

        def make():
            @env.PyAction()
            def render(sources, targets):
                return title

            return render

        with pytest.raises(PconsError) as caught:
            make()

        message = str(caught.value)
        assert message.startswith(str(caught.value.location) + ": ")
        assert (
            "PyAction render() reads title from the function it is nested in" in message
        )
        assert "Take it as a parameter and pass it at the call: " in message
        assert "render(target=..., title=...)." in message

    def test_a_global_says_to_move_the_import_into_the_body(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets):
                return Path(targets[0])

        message = str(caught.value)
        assert "PyAction render() uses Path from the build script" in message
        assert "Import Path inside the function body, the way this script" in message

    def test_a_default_says_to_drop_it_and_pass_it_at_the_call(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets, n=PYACTION_DEFAULT):
                return n

        message = str(caught.value)
        assert "PYACTION_DEFAULT is a parameter's default value" in message
        assert (
            "write the parameter without a default and pass it at the call" in message
        )
        assert "render(target=..., PYACTION_DEFAULT=PYACTION_DEFAULT)." in message

    def test_a_target_in_kwargs_points_at_source(self, project_env):
        """The first mistake: passing a target the way it reads naturally."""
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        @env.PyAction()
        def render(sources, targets, t):
            return t

        with pytest.raises(PconsError) as caught:
            call_line = inspect.currentframe().f_lineno + 1
            render(target="out.txt", t=made)

        message = str(caught.value)
        assert "argument t is the target 'made'" in message
        assert "the build description does not exist when the function runs" in message
        assert "List it in source= instead" in message
        assert caught.value.location.lineno == call_line

    def test_a_target_nested_in_kwargs_is_found_and_located(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        @env.PyAction()
        def render(sources, targets, inputs):
            return inputs

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", inputs={"first": [made]})

        assert "argument inputs['first'][0] is the target 'made'" in str(caught.value)

    def test_the_environment_in_kwargs_says_to_read_it_here(self, project_env):
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, e):
            return e

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", e=env)

        message = str(caught.value)
        assert "argument e is the environment itself" in message
        assert "Read what the function needs from it here" in message

    def test_an_unpicklable_kwarg_names_the_key_and_the_way_out(
        self, project_env, tmp_path
    ):
        _, env = project_env
        handle = (tmp_path / "src" / "main.c").open()

        @env.PyAction()
        def render(sources, targets, f):
            return f

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", f=handle)

        handle.close()
        message = str(caught.value)
        assert "cannot pickle argument f" in message
        assert "Pass what describes it instead, a path or a string" in message

    def test_two_edges_deriving_one_name_name_both_and_say_name(self, project_env):
        """The pickle is per edge, so two edges of one name collide on it."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets):
            return 1

        render(target="report.txt")

        with pytest.raises(PconsError) as caught:
            render(target="sub/report.txt")

        message = str(caught.value)
        assert "PyAction edge 'report' would overwrite" in message
        assert "build/pyact/report.args.pkl" in message
        assert "already written by the edge at " in message
        assert "test_user_errors.py:" in message.split("already written by")[1]
        assert 'Name one of the edges, name="something-else".' in message

    def test_one_function_decorated_twice_says_to_call_it_twice(self, project_env):
        """The reshape's own mistake: two decorations where one would do."""
        _, env = project_env

        def decorate():
            @env.PyAction()
            def render(sources, targets):
                return 1

            return render

        decorate()(target="a.txt")

        with pytest.raises(PconsError) as caught:
            decorate()(target="b.txt")

        message = str(caught.value)
        assert "would overwrite build/pyact/render.py" in message
        assert "Decorate the function once and call the action twice." in message
        assert "Rename" not in message

    def test_a_wrong_keyword_is_refused_at_the_call(self, project_env):
        """A build-time TypeError inside a generated module, moved forward."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", titel="typo")

        message = str(caught.value)
        assert "render(sources, targets, title) cannot be called with" in message
        assert "titel: missing a required argument: 'title'" in message
        assert "render(sources, targets, **kwargs)" in message
        assert caught.value.location.lineno > 0

    def test_a_missing_argument_is_refused_at_the_call(self, project_env):
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        with pytest.raises(PconsError, match="missing a required argument: 'title'"):
            render(target="out.txt")

    def test_a_reserved_parameter_name_is_refused_at_the_def(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets, source):
                return source

        message = str(caught.value)
        assert "PyAction render() has source as a parameter name" in message
        assert "the call spends that name on the edge itself" in message
        assert "Rename it in the def and at the call" in message

    def test_several_reserved_names_are_named_together(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets, depends, target):
                return depends, target

        message = str(caught.value)
        assert "PyAction render() has depends and target as parameter names" in message
        assert "the call spends those names on the edge itself" in message
        assert "Rename them in the def and at the call" in message

    def test_keyword_only_first_parameters_name_the_cause(self, project_env):
        """bind would say "too many positional arguments", which is our probe."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(*, sources, targets, title):
                return title

        message = str(caught.value)
        assert "cannot receive sources and targets" in message
        assert "no parameters that can be filled positionally" in message
        assert "def render(sources, targets, ...)." in message
        assert "positional arguments" not in message

    def test_one_positional_slot_is_still_one_too_few(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, *, targets):
                return 1

        assert "only one parameter that can be filled positionally" in str(caught.value)

    def test_a_positional_only_parameter_names_the_cause(self, project_env):
        """bind calls it "a required positional-only argument", to somebody
        who just typed that keyword."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets, title, /):
                return title

        message = str(caught.value)
        assert "cannot be given title: it is positional-only" in message
        assert "everything past sources and targets arrives as a keyword" in message
        assert "def render(sources, targets, /, title)." in message

    def test_two_positional_only_parameters_are_named_together(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets, title, count, /):
                return title, count

        message = str(caught.value)
        assert "cannot be given title and count: they are positional-only" in message
        assert "def render(sources, targets, /, title, count)." in message

    def test_a_slash_after_targets_is_accepted(self, project_env):
        """The good shape: only parameters past targets must take keywords."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, /, title):
            return title

        assert render(target="out.txt", title="t").name == "out"

    def test_sources_as_a_call_keyword_points_at_source(self, project_env):
        """bind would say "multiple values for argument 'sources'"."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        with pytest.raises(PconsError) as caught:
            render(target="o.txt", sources=["a.txt"], title="t")

        message = str(caught.value)
        assert "already receives sources from the edge" in message
        assert "the call cannot pass it as well" in message
        assert "The edge's own files are spelled source=" in message
        assert "multiple values" not in message

    def test_targets_as_a_call_keyword_points_at_target(self, project_env):
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, title):
            return title

        with pytest.raises(PconsError) as caught:
            render(target="o.txt", targets=["a.txt"], title="t")

        assert "The edge's own files are spelled target=" in str(caught.value)

    def test_a_var_keyword_body_may_still_take_a_sources_argument(self, project_env):
        """Only the function's own first two parameters are refused."""
        _, env = project_env

        @env.PyAction()
        def render(first, second, **rest):
            return rest

        assert render(target="o.txt", sources="a literal argument").name == "o"

    def test_a_parameter_named_env_is_fine(self, project_env):
        """env is not reserved: the call has no env= to collide with."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, env):
            return env

        made = render(target="out.txt", env="production")

        assert made.name == "out"

    def test_two_functions_of_one_name_say_to_rename_one(self, project_env, tmp_path):
        """The module is named after the function, so name= cannot part these."""
        _, env = project_env
        for sub in ("one", "two"):
            (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        first = build_script_function(
            tmp_path / "one",
            """
            def render(sources, targets):
                return 1
            """,
        )
        second = build_script_function(
            tmp_path / "two",
            """
            def render(sources, targets):
                return 2
            """,
        )
        env.PyAction()(first)(target="a.txt")

        with pytest.raises(PconsError) as caught:
            env.PyAction()(second)(target="b.txt")

        message = str(caught.value)
        assert "would overwrite build/pyact/render.py" in message
        assert "Rename one of the functions." in message
        assert "name=" not in message

    def test_a_partial_says_to_pass_the_function(self, project_env):
        _, env = project_env

        def render(sources, targets, n):
            return n

        with pytest.raises(PconsError) as caught:
            env.PyAction()(functools.partial(render, n=1))

        message = str(caught.value)
        assert "PyAction was given a functools.partial." in message
        assert "action(target=..., bound=value)." in message

    def test_a_bound_method_says_to_write_a_def(self, project_env):
        _, env = project_env

        class Holder:
            def render(self, sources, targets):
                return 1

        with pytest.raises(PconsError) as caught:
            env.PyAction()(Holder().render)

        message = str(caught.value)
        assert message.split(": ", 1)[1].startswith(
            "PyAction needs a function written in a build script, not "
        )
        assert "Write a def beside the other targets" in message

    def test_a_method_says_to_move_it_out_of_the_class(self, project_env):
        _, env = project_env

        class Holder:
            def render(self, sources, targets):
                return 1

        with pytest.raises(PconsError, match="move the def out of the class"):
            env.PyAction()(Holder.render)

    def test_a_coroutine_says_to_write_a_plain_def(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError, match="Write it as a plain def"):

            @env.PyAction()
            async def render(sources, targets):
                return 1

    def test_dunder_file_says_what_it_would_name(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyAction()
            def render(sources, targets):
                return __file__

        message = str(caught.value)
        assert "names the generated module rather than this script" in message
        assert (
            "Take the path it means as a parameter and pass it at the call" in message
        )
        assert 'render(target=..., here=project.root_dir / "...").' in message

    def test_every_refusal_names_the_build_script_and_line(self, project_env):
        """The location is half the message: the user has the script open."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:
            env.PyAction()(lambda sources, targets: None)

        location = caught.value.location
        assert location is not None
        assert location.filename.endswith("test_user_errors.py")
        assert location.lineno > 0
        assert str(caught.value).startswith(f"{location}: ")

    def test_a_script_local_helper_is_not_offered_an_import(
        self, project_env, tmp_path
    ):
        """__pcons__ is not importable, so no import can reach a helper."""
        _, env = project_env
        render = build_script_function(
            tmp_path,
            """
            def helper(value):
                return value


            def render(sources, targets):
                return helper(1)
            """,
        )

        with pytest.raises(PconsError) as caught:
            env.PyAction()(render)

        message = str(caught.value)
        assert "PyAction render() uses helper from the build script" in message
        assert "helper lives only in this build script" in message
        assert "write out what it does inside the function body" in message
        assert "import" not in message.split("nothing defines that name there.")[1][:40]

    def test_an_import_is_named_by_the_script_not_by_the_implementation(
        self, project_env, tmp_path
    ):
        """os.path.join is posixpath.join here and ntpath.join on Windows."""
        _, env = project_env
        render = build_script_function(
            tmp_path,
            """
            from os.path import join


            def render(sources, targets):
                return join("a", "b")
            """,
        )

        with pytest.raises(PconsError) as caught:
            env.PyAction()(render)

        message = str(caught.value)
        assert "Import join inside the function body, the way this script" in message
        assert "posixpath" not in message
        assert "ntpath" not in message

    def test_a_module_still_gets_its_import_line(self, project_env, tmp_path):
        _, env = project_env
        render = build_script_function(
            tmp_path,
            """
            import json


            def render(sources, targets):
                return json.dumps({})
            """,
        )

        with pytest.raises(PconsError, match='Write "import json"'):
            env.PyAction()(render)

    def test_a_tool_namespace_in_kwargs_points_at_its_values(self, project_env):
        """env.cc pickles, and drags the environment behind it."""
        _, env = project_env

        @env.PyAction()
        def render(sources, targets, cc):
            return cc

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", cc=env.cc)

        message = str(caught.value)
        assert "argument cc is the 'cc' tool namespace" in message
        assert "env.cc.flags rather than env.cc" in message

    def test_a_target_used_as_a_dict_key_is_found(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        @env.PyAction()
        def render(sources, targets, m):
            return m

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", m={made: 1})

        assert "a key of argument m is the target 'made'" in str(caught.value)

    def test_a_target_in_a_set_is_found_without_an_index(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        @env.PyAction()
        def render(sources, targets, s):
            return s

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", s={made})

        assert "an element of argument s is the target 'made'" in str(caught.value)

    def test_a_node_in_kwargs_points_at_source(self, project_env):
        """A node is the fifth build-description type, and reads as a path."""
        project, env = project_env

        @env.PyAction()
        def render(sources, targets, n):
            return n

        with pytest.raises(PconsError) as caught:
            render(target="out.txt", n=project.node("src/main.c"))

        message = str(caught.value)
        assert "argument n is the build graph's file 'src/main.c'" in message
        assert "List it in source= instead" in message

    def test_a_structure_that_contains_itself_is_refused_not_walked_forever(
        self, project_env
    ):
        """The walk's seen set is what makes this return instead of recursing."""
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )
        looping: dict[str, object] = {}
        looping["self"] = looping
        looping["t"] = made

        @env.PyAction()
        def render(sources, targets, loop):
            return loop

        with pytest.raises(PconsError, match="is the target 'made'"):
            render(target="out.txt", loop=looping)

    def test_a_renamed_lambda_says_its_source_is_not_a_def(self, project_env):
        """Reaches the ast fallback: __name__ says def, the source says lambda."""
        _, env = project_env
        renamed = lambda sources, targets: None  # noqa: E731
        renamed.__name__ = "renamed"

        with pytest.raises(PconsError) as caught:
            env.PyAction()(renamed)

        message = str(caught.value)
        assert "is not a def, it reads as Assign" in message
        assert "write one out in the build script" in message
