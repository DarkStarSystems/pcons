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


PYCOMMAND_DEFAULT = "a value the build script computed"


def build_script_function(tmp_path, source, name="render"):
    """Define a function the way a build script does, under ``__pcons__``.

    The module name is half of what PyCommand's messages reason about: a
    helper defined here has nowhere to be imported from, and a test module,
    which is importable, cannot stand in for that.
    """
    path = tmp_path / "pcons-build.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    namespace = {"__name__": RUN_NAME, "__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102
    return namespace[name]


class TestPyCommandErrors:
    """What env.PyCommand() says when a function cannot travel to build time.

    Every message is read here as the user reads it, whole, because the
    feature's failures are all configure-time refusals whose only job is to
    say what to type instead.
    """

    def test_a_lambda_says_to_write_a_def(self, project_env):
        _, env = project_env
        with pytest.raises(PconsError, match="write it as a def"):
            env.PyCommand(target="out.txt")(lambda sources, targets: None)

    def test_a_closure_names_the_variable_and_points_at_kwargs(self, project_env):
        _, env = project_env
        title = "report"

        def make():
            @env.PyCommand(target="out.txt")
            def render(sources, targets):
                return title

            return render

        with pytest.raises(PconsError) as caught:
            make()

        message = str(caught.value)
        assert "reads title from the function it is nested in" in message
        assert "Pass it in kwargs= and take it as an argument." in message

    def test_a_global_says_to_move_the_import_into_the_body(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt")
            def render(sources, targets):
                return Path(targets[0])

        message = str(caught.value)
        assert "uses Path from the build script" in message
        assert "Import Path inside the function body, the way this script" in message

    def test_a_default_says_to_drop_it_and_use_kwargs(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt")
            def render(sources, targets, n=PYCOMMAND_DEFAULT):
                return n

        message = str(caught.value)
        assert "PYCOMMAND_DEFAULT is a parameter's default value" in message
        assert "write the parameter without a default and pass" in message
        assert "in kwargs=" in message

    def test_a_target_in_kwargs_points_at_source(self, project_env):
        """The first mistake: passing a target the way it reads naturally."""
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"t": made})
            def render(sources, targets, t):
                return t

        message = str(caught.value)
        assert "kwargs['t'] is the target 'made'" in message
        assert "the build description does not exist when the function runs" in message
        assert "List it in source= instead" in message

    def test_a_target_nested_in_kwargs_is_found_and_located(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"inputs": {"first": [made]}})
            def render(sources, targets, inputs):
                return inputs

        assert "kwargs['inputs']['first'][0] is the target 'made'" in str(caught.value)

    def test_the_environment_in_kwargs_says_to_read_it_here(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"e": env})
            def render(sources, targets, e):
                return e

        message = str(caught.value)
        assert "kwargs['e'] is the environment itself" in message
        assert "Read what the function needs from it here" in message

    def test_an_unpicklable_kwarg_names_the_key_and_the_way_out(
        self, project_env, tmp_path
    ):
        _, env = project_env
        handle = (tmp_path / "src" / "main.c").open()

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"f": handle})
            def render(sources, targets, f):
                return f

        handle.close()
        message = str(caught.value)
        assert "cannot pickle kwargs f" in message
        assert "Pass what describes it instead, a path or a string" in message

    def test_two_commands_deriving_one_name_name_both_and_say_name(self, project_env):
        _, env = project_env

        @env.PyCommand(target="report.txt")
        def first(sources, targets):
            return 1

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="sub/report.txt")
            def second(sources, targets):
                return 2

        message = str(caught.value)
        assert "would overwrite build/pycmd/report.py" in message
        assert "test_user_errors.py:" in message.split("already written by")[1]
        assert "Pass name= to one of them" in message

    def test_a_partial_says_to_pass_the_function(self, project_env):
        _, env = project_env

        def render(sources, targets, n):
            return n

        with pytest.raises(PconsError) as caught:
            env.PyCommand(target="out.txt")(functools.partial(render, n=1))

        message = str(caught.value)
        assert "was given a functools.partial" in message
        assert "put its bound arguments in kwargs=" in message

    def test_a_bound_method_says_to_write_a_def(self, project_env):
        _, env = project_env

        class Holder:
            def render(self, sources, targets):
                return 1

        with pytest.raises(PconsError) as caught:
            env.PyCommand(target="out.txt")(Holder().render)

        message = str(caught.value)
        assert "needs a function written in a build script" in message
        assert "Write a def beside the other targets" in message

    def test_a_method_says_to_move_it_out_of_the_class(self, project_env):
        _, env = project_env

        class Holder:
            def render(self, sources, targets):
                return 1

        with pytest.raises(PconsError, match="move the def out of the class"):
            env.PyCommand(target="out.txt")(Holder.render)

    def test_a_coroutine_says_to_write_a_plain_def(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError, match="Write it as a plain def"):

            @env.PyCommand(target="out.txt")
            async def render(sources, targets):
                return 1

    def test_dunder_file_says_what_it_would_name(self, project_env):
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt")
            def render(sources, targets):
                return __file__

        message = str(caught.value)
        assert "names the generated module rather than this script" in message
        assert "Pass the path it means in kwargs=" in message

    def test_every_refusal_names_the_build_script_and_line(self, project_env):
        """The location is half the message: the user has the script open."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:
            env.PyCommand(target="out.txt")(lambda sources, targets: None)

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
            env.PyCommand(target="out.txt")(render)

        message = str(caught.value)
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
            env.PyCommand(target="out.txt")(render)

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
            env.PyCommand(target="out.txt")(render)

    def test_a_tool_namespace_in_kwargs_points_at_its_values(self, project_env):
        """env.cc pickles, and drags the environment behind it."""
        _, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"cc": env.cc})
            def render(sources, targets, cc):
                return cc

        message = str(caught.value)
        assert "kwargs['cc'] is the 'cc' tool namespace" in message
        assert "env.cc.flags rather than env.cc" in message

    def test_a_target_used_as_a_dict_key_is_found(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"m": {made: 1}})
            def render(sources, targets, m):
                return m

        assert "a key of kwargs['m'] is the target 'made'" in str(caught.value)

    def test_a_target_in_a_set_is_found_without_an_index(self, project_env):
        _, env = project_env
        made = env.Command(
            target="made.txt",
            source=["src/main.c"],
            command=["cp", "$SOURCE", "$TARGET"],
        )

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"s": {made}})
            def render(sources, targets, s):
                return s

        assert "an element of kwargs['s'] is the target 'made'" in str(caught.value)

    def test_a_node_in_kwargs_points_at_source(self, project_env):
        """A node is the fifth build-description type, and reads as a path."""
        project, env = project_env

        with pytest.raises(PconsError) as caught:

            @env.PyCommand(target="out.txt", kwargs={"n": project.node("src/main.c")})
            def render(sources, targets, n):
                return n

        message = str(caught.value)
        assert "kwargs['n'] is the build graph's file 'src/main.c'" in message
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

        with pytest.raises(PconsError, match="is the target 'made'"):

            @env.PyCommand(target="out.txt", kwargs={"loop": looping})
            def render(sources, targets, loop):
                return loop

    def test_a_renamed_lambda_says_its_source_is_not_a_def(self, project_env):
        """Reaches the ast fallback: __name__ says def, the source says lambda."""
        _, env = project_env
        renamed = lambda sources, targets: None  # noqa: E731
        renamed.__name__ = "renamed"

        with pytest.raises(PconsError) as caught:
            env.PyCommand(target="out.txt")(renamed)

        message = str(caught.value)
        assert "is not a def, it reads as Assign" in message
        assert "write one out in the build script" in message
