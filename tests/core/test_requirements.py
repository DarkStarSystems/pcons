# SPDX-License-Identifier: MIT
"""Tests for pcons.core.requirements."""

from pathlib import Path

from pcons.core.project import Project
from pcons.core.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)
from pcons.core.target import Target, UsageRequirements


class TestEffectiveRequirements:
    def test_creation(self):
        """Test basic creation of EffectiveRequirements."""
        reqs = EffectiveRequirements()
        assert reqs.includes == []
        assert reqs.defines == []
        assert reqs.compile_flags == []
        assert reqs.link_flags == []
        assert reqs.link_libs == []
        assert reqs.link_dirs == []

    def test_with_values(self):
        """Test creation with initial values."""
        reqs = EffectiveRequirements(
            includes=[Path("include")],
            defines=["DEBUG"],
            compile_flags=["-Wall"],
            link_flags=["-lm"],
            link_libs=["pthread"],
            link_dirs=[Path("/usr/lib")],
        )
        assert reqs.includes == [Path("include")]
        assert reqs.defines == ["DEBUG"]
        assert reqs.compile_flags == ["-Wall"]
        assert reqs.link_flags == ["-lm"]
        assert reqs.link_libs == ["pthread"]
        assert reqs.link_dirs == [Path("/usr/lib")]


class TestEffectiveRequirementsMerge:
    def test_merge_basic(self):
        """Test merging UsageRequirements into EffectiveRequirements."""
        eff = EffectiveRequirements()
        usage = UsageRequirements(
            include_dirs=[Path("inc1")],
            defines=["DEF1"],
            compile_flags=["-O2"],
            link_flags=["-static"],
            link_libs=["foo"],
        )
        eff.merge(usage)

        assert Path("inc1") in eff.includes
        assert "DEF1" in eff.defines
        assert "-O2" in eff.compile_flags
        assert "-static" in eff.link_flags
        assert "foo" in eff.link_libs

    def test_merge_avoids_duplicates(self):
        """Test that merge avoids duplicates."""
        eff = EffectiveRequirements(
            includes=[Path("inc")],
            defines=["DEF"],
        )
        usage = UsageRequirements(
            include_dirs=[Path("inc")],  # Same
            defines=["DEF"],  # Same
        )
        eff.merge(usage)

        assert eff.includes == [Path("inc")]
        assert eff.defines == ["DEF"]

    def test_merge_preserves_order(self):
        """Test that merge preserves order."""
        eff = EffectiveRequirements(includes=[Path("inc1")])
        usage1 = UsageRequirements(include_dirs=[Path("inc2")])
        usage2 = UsageRequirements(include_dirs=[Path("inc3")])

        eff.merge(usage1)
        eff.merge(usage2)

        assert eff.includes == [Path("inc1"), Path("inc2"), Path("inc3")]


class TestEffectiveRequirementsHashable:
    def test_as_hashable_tuple(self):
        """Test that as_hashable_tuple returns a hashable value."""
        reqs = EffectiveRequirements(
            includes=[Path("include")],
            defines=["DEBUG"],
            compile_flags=["-Wall"],
        )
        hashable = reqs.as_hashable_tuple()

        # Should be hashable (usable as dict key)
        d = {hashable: "test"}
        assert d[hashable] == "test"

    def test_same_requirements_same_hash(self):
        """Test that identical requirements produce the same hash."""
        reqs1 = EffectiveRequirements(
            includes=[Path("include")],
            defines=["DEBUG"],
        )
        reqs2 = EffectiveRequirements(
            includes=[Path("include")],
            defines=["DEBUG"],
        )

        assert reqs1.as_hashable_tuple() == reqs2.as_hashable_tuple()

    def test_different_requirements_different_hash(self):
        """Test that different requirements produce different hashes."""
        reqs1 = EffectiveRequirements(defines=["DEBUG"])
        reqs2 = EffectiveRequirements(defines=["RELEASE"])

        assert reqs1.as_hashable_tuple() != reqs2.as_hashable_tuple()


class TestEffectiveRequirementsClone:
    def test_clone(self):
        """Test that clone creates a deep copy."""
        reqs = EffectiveRequirements(
            includes=[Path("inc")],
            defines=["DEF"],
        )
        clone = reqs.clone()

        assert clone.includes == reqs.includes
        assert clone.defines == reqs.defines

        # Modifying clone doesn't affect original
        clone.includes.append(Path("other"))
        assert Path("other") not in reqs.includes


class TestComputeEffectiveRequirements:
    def test_basic_computation(self):
        """Test basic effective requirements computation."""
        project = Project("test")
        env = project.Environment()

        target = Target("mylib", target_type="static_library")
        target._env = env
        target.private.defines.append("BUILDING_MYLIB")

        effective = compute_effective_requirements(target, env)

        assert "BUILDING_MYLIB" in effective.defines

    def test_includes_private_requirements(self):
        """Test that private requirements are included."""
        project = Project("test")
        env = project.Environment()

        target = Target("mylib", target_type="static_library")
        target._env = env
        target.private.include_dirs.append(Path("src"))
        target.private.defines.append("PRIVATE_DEF")
        target.private.compile_flags.append("-Werror")

        effective = compute_effective_requirements(target, env)

        assert Path("src") in effective.includes
        assert "PRIVATE_DEF" in effective.defines
        assert "-Werror" in effective.compile_flags

    def test_includes_dependency_public_requirements(self):
        """Test that dependency public requirements are included."""
        project = Project("test")
        env = project.Environment()

        # Create a dependency library
        dep = Target("dep", target_type="static_library")
        dep._env = env
        dep.public.include_dirs.append(Path("dep/include"))
        dep.public.defines.append("DEP_API")

        # Create the target that depends on it
        target = Target("mylib", target_type="static_library")
        target._env = env
        target.link(dep)

        effective = compute_effective_requirements(target, env)

        assert Path("dep/include") in effective.includes
        assert "DEP_API" in effective.defines

    def test_transitive_requirements(self):
        """Test that transitive requirements are collected."""
        project = Project("test")
        env = project.Environment()

        # Create a chain: target -> libB -> libA
        libA = Target("libA", target_type="static_library")
        libA._env = env
        libA.public.include_dirs.append(Path("libA/include"))
        libA.public.defines.append("LIBA_API")

        libB = Target("libB", target_type="static_library")
        libB._env = env
        libB.public.include_dirs.append(Path("libB/include"))
        libB.link(libA)

        target = Target("app", target_type="program")
        target._env = env
        target.link(libB)

        effective = compute_effective_requirements(target, env)

        # Should have requirements from both libA and libB
        assert Path("libA/include") in effective.includes
        assert Path("libB/include") in effective.includes
        assert "LIBA_API" in effective.defines

    def test_private_not_propagated(self):
        """Test that private requirements don't propagate to dependents."""
        project = Project("test")
        env = project.Environment()

        # Create a dependency with private requirements
        dep = Target("dep", target_type="static_library")
        dep._env = env
        dep.private.defines.append("PRIVATE_DEF")
        dep.public.defines.append("PUBLIC_DEF")

        # Create the target that depends on it
        target = Target("app", target_type="program")
        target._env = env
        target.link(dep)

        effective = compute_effective_requirements(target, env)

        # Should have public but not private
        assert "PUBLIC_DEF" in effective.defines
        assert "PRIVATE_DEF" not in effective.defines
