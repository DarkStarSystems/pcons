# SPDX-License-Identifier: MIT
"""Tests for pcons.core.flags module."""

from pcons.core.flags import (
    DEFAULT_SEPARATED_ARG_FLAGS,
    FlagPair,
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


class TestFlagPair:
    """Tests for FlagPair class."""

    def test_creation(self):
        """Test FlagPair creation."""
        fp = FlagPair("-include", "header.h")
        assert fp.flag == "-include"
        assert fp.argument == "header.h"

    def test_iteration(self):
        """Test FlagPair iteration."""
        fp = FlagPair("-include", "header.h")
        items = list(fp)
        assert items == ["-include", "header.h"]

    def test_unpacking(self):
        """Test FlagPair unpacking."""
        fp = FlagPair("-include", "header.h")
        flag, arg = fp
        assert flag == "-include"
        assert arg == "header.h"

    def test_immutability(self):
        """Test FlagPair is immutable (frozen dataclass)."""
        fp = FlagPair("-include", "header.h")
        import pytest

        with pytest.raises(AttributeError):
            fp.flag = "-other"  # type: ignore[misc]

    def test_equality(self):
        """Test FlagPair equality."""
        fp1 = FlagPair("-include", "header.h")
        fp2 = FlagPair("-include", "header.h")
        fp3 = FlagPair("-include", "other.h")
        assert fp1 == fp2
        assert fp1 != fp3

    def test_hashable(self):
        """Test FlagPair is hashable (can be used in sets)."""
        fp1 = FlagPair("-include", "header.h")
        fp2 = FlagPair("-include", "header.h")
        fp3 = FlagPair("-include", "other.h")
        s = {fp1, fp2, fp3}
        assert len(s) == 2


class TestFlagPairDeduplication:
    """Tests for FlagPair handling in deduplicate_flags."""

    def test_flag_pair_basic_dedup(self):
        """Test that duplicate FlagPairs are removed."""
        flags = [
            FlagPair("-custom", "val1"),
            "-Wall",
            FlagPair("-custom", "val1"),  # duplicate
        ]
        result = deduplicate_flags(flags)
        assert result == ["-custom", "val1", "-Wall"]

    def test_flag_pair_different_values_kept(self):
        """Test that FlagPairs with different values are kept."""
        flags = [
            FlagPair("-custom", "val1"),
            FlagPair("-custom", "val2"),
        ]
        result = deduplicate_flags(flags)
        assert result == ["-custom", "val1", "-custom", "val2"]

    def test_flag_pair_mixed_with_strings(self):
        """Test FlagPair mixed with string flags."""
        flags = [
            "-Wall",
            FlagPair("-include", "header.h"),
            "-O2",
            FlagPair("-include", "other.h"),
        ]
        result = deduplicate_flags(flags)
        assert result == ["-Wall", "-include", "header.h", "-O2", "-include", "other.h"]

    def test_flag_pair_expanded_to_strings(self):
        """Test that FlagPairs are expanded to strings in output."""
        flags = [FlagPair("-include", "header.h")]
        result = deduplicate_flags(flags)
        assert result == ["-include", "header.h"]
        assert all(isinstance(f, str) for f in result)


class TestFlagPairMerge:
    """Tests for FlagPair handling in merge_flags."""

    def test_merge_flag_pair_into_empty(self):
        """Test merging FlagPair into empty list."""
        existing: list[str] = []
        merge_flags(existing, [FlagPair("-include", "header.h")])
        assert existing == ["-include", "header.h"]

    def test_merge_flag_pair_no_duplicate(self):
        """Test FlagPair doesn't duplicate existing pair."""
        existing: list[str] = ["-include", "header.h"]
        merge_flags(
            existing,
            [FlagPair("-include", "header.h")],
            frozenset(["-include"]),
        )
        assert existing == ["-include", "header.h"]

    def test_merge_flag_pair_adds_new(self):
        """Test FlagPair adds new pair."""
        existing: list[str] = ["-include", "header1.h"]
        merge_flags(
            existing,
            [FlagPair("-include", "header2.h")],
            frozenset(["-include"]),
        )
        assert existing == ["-include", "header1.h", "-include", "header2.h"]


class TestIncludeFlagPairs:
    """Tests for -include flag handling (the original bug)."""

    def test_include_flags_with_separated_arg_flags(self):
        """Test that -include flags are properly deduplicated as pairs."""
        # This is the original bug: -include was being deduplicated incorrectly
        # because it wasn't in SEPARATED_ARG_FLAGS
        separated = frozenset(["-include"])
        flags = ["-include", "header1.h", "-include", "header2.h"]
        result = deduplicate_flags(flags, separated)
        assert result == ["-include", "header1.h", "-include", "header2.h"]

    def test_include_flags_duplicate_removed(self):
        """Test that duplicate -include pairs are removed."""
        separated = frozenset(["-include"])
        flags = [
            "-include",
            "header1.h",
            "-include",
            "header2.h",
            "-include",
            "header1.h",
        ]
        result = deduplicate_flags(flags, separated)
        assert result == ["-include", "header1.h", "-include", "header2.h"]

    def test_include_in_unix_separated_flags(self):
        """Test that -include is now in Unix toolchain's SEPARATED_ARG_FLAGS."""
        from pcons.toolchains.unix import UnixToolchain

        assert "-include" in UnixToolchain.SEPARATED_ARG_FLAGS
        assert "-imacros" in UnixToolchain.SEPARATED_ARG_FLAGS
        assert "-x" in UnixToolchain.SEPARATED_ARG_FLAGS

    def test_gcc_toolchain_has_include(self):
        """Test that GCC toolchain has -include flag."""
        from pcons.toolchains.gcc import GccToolchain

        toolchain = GccToolchain()
        flags = toolchain.get_separated_arg_flags()
        assert "-include" in flags

    def test_llvm_toolchain_has_include(self):
        """Test that LLVM toolchain has -include flag."""
        from pcons.toolchains.llvm import LlvmToolchain

        toolchain = LlvmToolchain()
        flags = toolchain.get_separated_arg_flags()
        assert "-include" in flags
