# SPDX-License-Identifier: MIT
"""Tests for user error experience in pcons.

Tests ~50 plausible user mistakes and verifies pcons gives helpful,
actionable error messages. Tests marked with xfail indicate known gaps
in error handling where pcons currently produces unhelpful errors or
silently accepts invalid input.

These serve as both regression tests for existing good errors and a
roadmap for future error handling improvements.
"""

from pathlib import Path

import pytest

from pcons.core.errors import (
    BuilderError,
    DependencyCycleError,
    MissingSourceError,
    MissingVariableError,
    PconsError,
)
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

    def test_link_string_instead_of_target(self, project_env):
        """User passes a string library name instead of a Target object."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises(TypeError, match="[Tt]arget"):
            app.link("mylib")

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

    def test_environment_toolchain_string(self, project_env):
        """User passes a string like "gcc" instead of find_c_toolchain()."""
        project, _ = project_env
        with pytest.raises(TypeError, match="toolchain"):
            project.Environment(toolchain="gcc")

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

    def test_typo_usage_requirement_name_is_silent(self, project_env):
        """Typo in usage requirement name is silently accepted.

        UsageRequirements is intentionally open-ended: any toolchain can
        define its own requirement names (e.g., device_flags, module_dirs).
        A typo like 'includedirs' creates an unused list, but this is by
        design -- restricting to known names would break extensibility.
        Users will notice via missing flags in build output.
        """
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        # Typo silently creates a new attribute -- this is expected behavior
        app.public.includedirs.append("/usr/include")
        assert app.public.includedirs == ["/usr/include"]
        # The correctly-spelled one remains empty
        assert app.public.include_dirs == []

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
        project, env = project_env
        # This succeeds but produces a useless empty build file
        project.generate()
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
            app.link(lib)

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
        """Two targets depending on each other."""
        project, env = project_env
        lib_a = project.StaticLibrary("liba", env, sources=["src/lib.c"])
        lib_b = project.StaticLibrary("libb", env, sources=["src/main.c"])
        lib_a.link(lib_b)
        lib_b.link(lib_a)
        errors = project.validate()
        assert any(isinstance(e, DependencyCycleError) for e in errors)

    def test_circular_dependency_strict(self, project_env):
        """Circular dependency in strict mode raises."""
        project, env = project_env
        lib_a = project.StaticLibrary("liba", env, sources=["src/lib.c"])
        lib_b = project.StaticLibrary("libb", env, sources=["src/main.c"])
        lib_a.link(lib_b)
        lib_b.link(lib_a)
        with pytest.raises(PconsError):
            project.resolve(strict=True)

    def test_self_link(self, project_env):
        """Target links itself."""
        project, env = project_env
        app = project.Program("app", env, sources=["src/main.c"])
        with pytest.raises((DependencyCycleError, ValueError), match="self|cycle"):
            app.link(app)

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
        app.link(lib)
        app.link(lib)
        # Should not crash and lib should appear only once
        assert app.dependencies.count(lib) == 1


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
