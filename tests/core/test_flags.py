# SPDX-License-Identifier: MIT
"""Tests for pcons.core.flags module."""

from pcons.core.flags import (
    DEFAULT_SEPARATED_ARG_FLAGS,
    deduplicate_flags,
    get_separated_arg_flags_from_toolchains,
    is_separated_arg_flag,
    merge_flags,
)

# Define a test set of separated arg flags (similar to what GCC/LLVM toolchains use)
TEST_SEPARATED_ARG_FLAGS: frozenset[str] = frozenset(
    [
        "-F",
        "-framework",
        "-arch",
        "-target",
        "-isystem",
        "-Xlinker",
        "-I",  # For testing purposes
    ]
)


class TestIsSeparatedArgFlag:
    """Tests for is_separated_arg_flag function."""

    def test_framework_flag(self):
        """Test that -F is recognized as a separated arg flag."""
        assert is_separated_arg_flag("-F", TEST_SEPARATED_ARG_FLAGS)

    def test_framework_long_flag(self):
        """Test that -framework is recognized as a separated arg flag."""
        assert is_separated_arg_flag("-framework", TEST_SEPARATED_ARG_FLAGS)

    def test_simple_flags_not_separated(self):
        """Test that simple flags are not recognized as separated arg flags."""
        assert not is_separated_arg_flag("-O2", TEST_SEPARATED_ARG_FLAGS)
        assert not is_separated_arg_flag("-Wall", TEST_SEPARATED_ARG_FLAGS)
        assert not is_separated_arg_flag("-g", TEST_SEPARATED_ARG_FLAGS)

    def test_attached_arg_flags_not_separated(self):
        """Test that flags with attached args are not separated arg flags."""
        assert not is_separated_arg_flag("-DFOO", TEST_SEPARATED_ARG_FLAGS)
        assert not is_separated_arg_flag("-I/path", TEST_SEPARATED_ARG_FLAGS)
        assert not is_separated_arg_flag("-L/lib", TEST_SEPARATED_ARG_FLAGS)

    def test_known_separated_flags(self):
        """Test all known separated arg flags."""
        for flag in ["-F", "-framework", "-arch", "-target", "-isystem", "-Xlinker"]:
            assert is_separated_arg_flag(flag, TEST_SEPARATED_ARG_FLAGS), (
                f"{flag} should be a separated arg flag"
            )

    def test_default_empty_set(self):
        """Test that default separated arg flags is empty."""
        assert DEFAULT_SEPARATED_ARG_FLAGS == frozenset()
        # With default (empty set), nothing is a separated arg flag
        assert not is_separated_arg_flag("-F")
        assert not is_separated_arg_flag("-framework")


class TestDeduplicateFlags:
    """Tests for deduplicate_flags function."""

    def test_empty_list(self):
        """Test with empty list."""
        assert deduplicate_flags([], TEST_SEPARATED_ARG_FLAGS) == []

    def test_simple_flags_dedup(self):
        """Test de-duplication of simple flags."""
        result = deduplicate_flags(
            ["-O2", "-Wall", "-O2", "-g", "-Wall"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-O2", "-Wall", "-g"]

    def test_separated_arg_flags_same_arg(self):
        """Test that identical flag+arg pairs are de-duplicated."""
        result = deduplicate_flags(
            ["-F", "path1", "-F", "path1"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-F", "path1"]

    def test_separated_arg_flags_different_args(self):
        """Test that different flag+arg pairs are preserved."""
        result = deduplicate_flags(
            ["-F", "path1", "-F", "path2"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-F", "path1", "-F", "path2"]

    def test_framework_flag_different_frameworks(self):
        """Test -framework with different frameworks."""
        result = deduplicate_flags(
            ["-framework", "Cocoa", "-framework", "CoreFoundation"],
            TEST_SEPARATED_ARG_FLAGS,
        )
        assert result == ["-framework", "Cocoa", "-framework", "CoreFoundation"]

    def test_framework_flag_same_framework(self):
        """Test -framework with same framework is de-duplicated."""
        result = deduplicate_flags(
            ["-framework", "Cocoa", "-framework", "Cocoa"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-framework", "Cocoa"]

    def test_mixed_flags(self):
        """Test with mixed simple and paired flags."""
        result = deduplicate_flags(
            ["-O2", "-F", "path1", "-Wall", "-F", "path2", "-O2", "-F", "path1"],
            TEST_SEPARATED_ARG_FLAGS,
        )
        assert result == ["-O2", "-F", "path1", "-Wall", "-F", "path2"]

    def test_arch_flags(self):
        """Test -arch flags (common on macOS)."""
        result = deduplicate_flags(
            ["-arch", "x86_64", "-arch", "arm64"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-arch", "x86_64", "-arch", "arm64"]

    def test_arch_flags_duplicate(self):
        """Test duplicate -arch flags are removed."""
        result = deduplicate_flags(
            ["-arch", "x86_64", "-arch", "x86_64"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-arch", "x86_64"]

    def test_preserves_order(self):
        """Test that first occurrence is preserved."""
        result = deduplicate_flags(
            ["-Wall", "-Werror", "-Wall"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-Wall", "-Werror"]

    def test_isystem_flags(self):
        """Test -isystem flags."""
        result = deduplicate_flags(
            ["-isystem", "/inc1", "-isystem", "/inc2"], TEST_SEPARATED_ARG_FLAGS
        )
        assert result == ["-isystem", "/inc1", "-isystem", "/inc2"]

    def test_without_separated_flags(self):
        """Test that without separated arg flags, all are treated as simple flags."""
        # Without the flag set, -F and path are treated as separate simple flags
        result = deduplicate_flags(["-F", "path1", "-F", "path1"])
        assert result == ["-F", "path1"]  # Still deduped, but as separate items


class TestMergeFlags:
    """Tests for merge_flags function."""

    def test_merge_into_empty(self):
        """Test merging into empty list."""
        existing: list[str] = []
        merge_flags(existing, ["-O2", "-Wall"], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-O2", "-Wall"]

    def test_merge_empty(self):
        """Test merging empty list."""
        existing = ["-O2"]
        merge_flags(existing, [], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-O2"]

    def test_merge_no_duplicates(self):
        """Test merging with no overlapping flags."""
        existing = ["-O2"]
        merge_flags(existing, ["-Wall", "-g"], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-O2", "-Wall", "-g"]

    def test_merge_with_duplicates(self):
        """Test merging with overlapping simple flags."""
        existing = ["-O2", "-Wall"]
        merge_flags(existing, ["-Wall", "-g"], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-O2", "-Wall", "-g"]

    def test_merge_paired_flags_no_duplicates(self):
        """Test merging paired flags with no overlap."""
        existing = ["-F", "path1"]
        merge_flags(existing, ["-F", "path2"], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-F", "path1", "-F", "path2"]

    def test_merge_paired_flags_with_duplicates(self):
        """Test merging paired flags with overlap."""
        existing = ["-F", "path1", "-F", "path2"]
        merge_flags(existing, ["-F", "path1", "-F", "path3"], TEST_SEPARATED_ARG_FLAGS)
        assert existing == ["-F", "path1", "-F", "path2", "-F", "path3"]

    def test_merge_framework_flags(self):
        """Test merging -framework flags."""
        existing = ["-framework", "Cocoa"]
        merge_flags(
            existing,
            ["-framework", "CoreFoundation", "-framework", "Cocoa"],
            TEST_SEPARATED_ARG_FLAGS,
        )
        assert existing == ["-framework", "Cocoa", "-framework", "CoreFoundation"]

    def test_merge_mixed_flags(self):
        """Test merging mixed simple and paired flags."""
        existing = ["-O2", "-F", "path1"]
        merge_flags(
            existing,
            ["-Wall", "-F", "path2", "-O2", "-F", "path1"],
            TEST_SEPARATED_ARG_FLAGS,
        )
        assert existing == ["-O2", "-F", "path1", "-Wall", "-F", "path2"]

    def test_merge_modifies_in_place(self):
        """Test that merge_flags modifies the list in place."""
        existing = ["-O2"]
        original_id = id(existing)
        merge_flags(existing, ["-Wall"], TEST_SEPARATED_ARG_FLAGS)
        assert id(existing) == original_id
        assert existing == ["-O2", "-Wall"]


class TestIntegrationWithUsageRequirements:
    """Integration tests with UsageRequirements."""

    def test_usage_requirements_merge_preserves_paired_flags(self):
        """Test that UsageRequirements.merge handles paired flags correctly."""
        from pcons.core.target import UsageRequirements

        req1 = UsageRequirements(link_flags=["-F", "path1", "-framework", "Cocoa"])
        req2 = UsageRequirements(link_flags=["-F", "path2", "-framework", "Cocoa"])

        req1.merge(req2, TEST_SEPARATED_ARG_FLAGS)

        # -framework Cocoa should not be duplicated
        # -F path1 and -F path2 should both be present
        assert req1.link_flags == [
            "-F",
            "path1",
            "-framework",
            "Cocoa",
            "-F",
            "path2",
        ]

    def test_usage_requirements_merge_compile_flags(self):
        """Test that compile flags are also handled correctly."""
        from pcons.core.target import UsageRequirements

        req1 = UsageRequirements(compile_flags=["-isystem", "/inc1"])
        req2 = UsageRequirements(
            compile_flags=["-isystem", "/inc2", "-isystem", "/inc1"]
        )

        req1.merge(req2, TEST_SEPARATED_ARG_FLAGS)

        # /inc1 should not be duplicated, /inc2 should be added
        assert req1.compile_flags == ["-isystem", "/inc1", "-isystem", "/inc2"]


class TestIntegrationWithEffectiveRequirements:
    """Integration tests with EffectiveRequirements."""

    def test_effective_requirements_merge_preserves_paired_flags(self):
        """Test that EffectiveRequirements.merge handles paired flags correctly."""
        from pcons.core.requirements import EffectiveRequirements
        from pcons.core.target import UsageRequirements

        eff = EffectiveRequirements(
            link_flags=["-F", "path1"], separated_arg_flags=TEST_SEPARATED_ARG_FLAGS
        )
        usage = UsageRequirements(link_flags=["-F", "path2", "-F", "path1"])

        eff.merge(usage)

        # path1 should not be duplicated, path2 should be added
        assert eff.link_flags == ["-F", "path1", "-F", "path2"]

    def test_real_world_macos_frameworks(self):
        """Test a realistic macOS scenario with multiple frameworks."""
        from pcons.core.requirements import EffectiveRequirements
        from pcons.core.target import UsageRequirements

        # Simulating what happens when multiple dependencies each need frameworks
        eff = EffectiveRequirements(separated_arg_flags=TEST_SEPARATED_ARG_FLAGS)

        # First library needs CoreFoundation
        lib1_usage = UsageRequirements(
            link_flags=["-framework", "CoreFoundation", "-F", "/Library/Frameworks"]
        )
        eff.merge(lib1_usage)

        # Second library needs AppKit and CoreFoundation
        lib2_usage = UsageRequirements(
            link_flags=[
                "-framework",
                "AppKit",
                "-framework",
                "CoreFoundation",  # duplicate
                "-F",
                "/System/Library/Frameworks",
            ]
        )
        eff.merge(lib2_usage)

        # CoreFoundation should appear only once
        # Both -F paths should be present
        assert eff.link_flags == [
            "-framework",
            "CoreFoundation",
            "-F",
            "/Library/Frameworks",
            "-framework",
            "AppKit",
            "-F",
            "/System/Library/Frameworks",
        ]


class TestGetSeparatedArgFlagsFromToolchains:
    """Tests for get_separated_arg_flags_from_toolchains function."""

    def test_empty_toolchains(self):
        """Test with no toolchains."""
        result = get_separated_arg_flags_from_toolchains([])
        assert result == frozenset()

    def test_toolchain_without_method(self):
        """Test with object that doesn't have get_separated_arg_flags method."""

        class FakeToolchain:
            pass

        result = get_separated_arg_flags_from_toolchains([FakeToolchain()])
        assert result == frozenset()

    def test_single_toolchain(self):
        """Test with a single toolchain."""

        class FakeToolchain:
            def get_separated_arg_flags(self) -> frozenset[str]:
                return frozenset(["-F", "-framework"])

        result = get_separated_arg_flags_from_toolchains([FakeToolchain()])
        assert result == frozenset(["-F", "-framework"])

    def test_multiple_toolchains_union(self):
        """Test that flags from multiple toolchains are combined."""

        class Toolchain1:
            def get_separated_arg_flags(self) -> frozenset[str]:
                return frozenset(["-F", "-framework"])

        class Toolchain2:
            def get_separated_arg_flags(self) -> frozenset[str]:
                return frozenset(["-arch", "-target"])

        result = get_separated_arg_flags_from_toolchains([Toolchain1(), Toolchain2()])
        assert result == frozenset(["-F", "-framework", "-arch", "-target"])

    def test_with_gcc_toolchain(self):
        """Test with actual GCC toolchain."""
        from pcons.toolchains.gcc import GccToolchain

        toolchain = GccToolchain()
        result = get_separated_arg_flags_from_toolchains([toolchain])
        assert "-F" in result
        assert "-framework" in result
        assert "-arch" in result
        assert "-isystem" in result

    def test_with_llvm_toolchain(self):
        """Test with actual LLVM toolchain."""
        from pcons.toolchains.llvm import LlvmToolchain

        toolchain = LlvmToolchain()
        result = get_separated_arg_flags_from_toolchains([toolchain])
        assert "-F" in result
        assert "-framework" in result
        assert "-arch" in result
        assert "-isystem" in result

    def test_with_msvc_toolchain(self):
        """Test with actual MSVC toolchain."""
        from pcons.toolchains.msvc import MsvcToolchain

        toolchain = MsvcToolchain()
        result = get_separated_arg_flags_from_toolchains([toolchain])
        assert "/link" in result
        # MSVC has fewer separated arg flags since it uses /FLAG:value syntax
        assert "-F" not in result

    def test_with_clang_cl_toolchain(self):
        """Test with actual Clang-CL toolchain."""
        from pcons.toolchains.clang_cl import ClangClToolchain

        toolchain = ClangClToolchain()
        result = get_separated_arg_flags_from_toolchains([toolchain])
        assert "/link" in result
        assert "-target" in result
