# SPDX-License-Identifier: MIT
"""Test for mixed C/C++ target flag leakage bug.

Bug: When a target contains both .c and .cpp sources, and the user sets
language-specific flags only on env.cxx.flags (e.g., -std=c++20), those
flags leak to .c file compilation.

Root cause: compute_effective_requirements() uses _get_primary_tool() to
determine the "primary" tool for the target. For mixed C/C++ targets, this
returns "cxx". The cxx.flags (including -std=c++20) are then stored in
EffectiveRequirements.compile_flags. When the resolver expands the compile
command for a .c file (tool_name="cc"), it applies these cxx-derived flags
as cc.flags overrides via tool_overrides[f"{tool_name}.flags"], causing
-std=c++20 to appear in the C compiler command where it is invalid.

The fix should ensure that language-specific flags from env.cc.flags are
used for .c files and env.cxx.flags for .cpp files, even in mixed targets.
"""

import pytest

from pcons.core.project import Project
from pcons.toolchains.llvm import (
    ClangCCompiler,
    ClangCxxCompiler,
    LlvmArchiver,
    LlvmLinker,
    LlvmToolchain,
)


@pytest.fixture
def llvm_toolchain():
    """Create a pre-configured LLVM toolchain for testing."""
    toolchain = LlvmToolchain()
    # Manually populate tools (normally done by _configure_tools via clang detection)
    toolchain._tools = {
        "cc": ClangCCompiler(),
        "cxx": ClangCxxCompiler(),
        "ar": LlvmArchiver(),
        "link": LlvmLinker(),
    }
    toolchain._configured = True
    return toolchain


class TestMixedLanguageFlagLeakage:
    """Test that C++-only flags don't leak to C compilation in mixed targets."""

    def test_cxx_std_flag_does_not_leak_to_c(self, tmp_path, llvm_toolchain):
        """C++ standard flag set on cxx.flags must not appear in cc commands.

        This is the core bug: -std=c++20 added only to env.cxx.flags ends up
        in the compile command for .c files, causing 'error: invalid argument
        -std=c++20 not allowed with C'.
        """
        # Create source files
        (tmp_path / "main.cpp").write_text("int main() { return 0; }")
        (tmp_path / "util.c").write_text("int helper(void) { return 1; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=llvm_toolchain)

        # Add -std=c++20 ONLY to C++ flags (not C)
        env.cxx.flags.append("-std=c++20")

        # Verify the flags are on separate lists
        assert "-std=c++20" in env.cxx.flags
        assert "-std=c++20" not in env.cc.flags

        # Build a program with mixed C and C++ sources
        target = project.Program("myapp", env, sources=["main.cpp", "util.c"])
        project.resolve()

        # Find the object node for util.c (the C file)
        c_obj = None
        cpp_obj = None
        for obj in target.object_nodes:
            if "util" in str(obj.path):
                c_obj = obj
            elif "main" in str(obj.path):
                cpp_obj = obj

        assert c_obj is not None, "Should have an object node for util.c"
        assert cpp_obj is not None, "Should have an object node for main.cpp"

        # Check the expanded commands
        c_command = c_obj._build_info.get("command", [])
        cpp_command = cpp_obj._build_info.get("command", [])

        # The C++ object should have -std=c++20
        cpp_cmd_str = " ".join(str(t) for t in cpp_command)
        assert "-std=c++20" in cpp_cmd_str, (
            f"C++ command should contain -std=c++20, got: {cpp_cmd_str}"
        )

        # The C object must NOT have -std=c++20 — this is the bug
        c_cmd_str = " ".join(str(t) for t in c_command)
        assert "-std=c++20" not in c_cmd_str, (
            f"C command must not contain -std=c++20 (leaked from cxx.flags), "
            f"got: {c_cmd_str}"
        )

    def test_separate_cc_cxx_flags_preserved(self, tmp_path, llvm_toolchain):
        """Each language's flags should be independently preserved."""
        (tmp_path / "app.cpp").write_text("int main() { return 0; }")
        (tmp_path / "legacy.c").write_text("int old(void) { return 1; }")

        project = Project("test", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=llvm_toolchain)

        # Set different standards for C and C++
        env.cxx.flags.append("-std=c++20")
        env.cc.flags.append("-std=c17")

        target = project.Program("myapp", env, sources=["app.cpp", "legacy.c"])
        project.resolve()

        c_obj = None
        cpp_obj = None
        for obj in target.object_nodes:
            if "legacy" in str(obj.path):
                c_obj = obj
            elif "app" in str(obj.path):
                cpp_obj = obj

        assert c_obj is not None
        assert cpp_obj is not None

        c_command = c_obj._build_info.get("command", [])
        cpp_command = cpp_obj._build_info.get("command", [])

        c_cmd_str = " ".join(str(t) for t in c_command)
        cpp_cmd_str = " ".join(str(t) for t in cpp_command)

        # C++ should have c++20, not c17
        assert "-std=c++20" in cpp_cmd_str
        assert "-std=c17" not in cpp_cmd_str

        # C should have c17, not c++20
        assert "-std=c17" in c_cmd_str, (
            f"C command should contain -std=c17, got: {c_cmd_str}"
        )
        assert "-std=c++20" not in c_cmd_str, (
            f"C command must not contain -std=c++20, got: {c_cmd_str}"
        )
