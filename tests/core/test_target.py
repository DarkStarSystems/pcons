# SPDX-License-Identifier: MIT
"""Tests for pcons.core.target."""

from pathlib import Path

from pcons.core.target import ImportedTarget, Target, UsageRequirements


class TestUsageRequirements:
    def test_creation(self):
        req = UsageRequirements()
        assert req.include_dirs == []
        assert req.link_libs == []
        assert req.defines == []

    def test_with_values(self):
        req = UsageRequirements(
            include_dirs=[Path("include")],
            link_libs=["foo"],
            defines=["DEBUG"],
        )
        assert req.include_dirs == [Path("include")]
        assert req.link_libs == ["foo"]
        assert req.defines == ["DEBUG"]

    def test_merge(self):
        req1 = UsageRequirements(
            include_dirs=[Path("inc1")],
            defines=["DEF1"],
        )
        req2 = UsageRequirements(
            include_dirs=[Path("inc2")],
            defines=["DEF2"],
        )
        req1.merge(req2)

        assert req1.include_dirs == [Path("inc1"), Path("inc2")]
        assert req1.defines == ["DEF1", "DEF2"]

    def test_merge_avoids_duplicates(self):
        req1 = UsageRequirements(
            include_dirs=[Path("inc")],
            defines=["DEF"],
        )
        req2 = UsageRequirements(
            include_dirs=[Path("inc")],  # Same
            defines=["DEF"],  # Same
        )
        req1.merge(req2)

        assert req1.include_dirs == [Path("inc")]
        assert req1.defines == ["DEF"]

    def test_clone(self):
        req = UsageRequirements(
            include_dirs=[Path("inc")],
            link_libs=["foo"],
        )
        clone = req.clone()

        assert clone.include_dirs == req.include_dirs
        assert clone.link_libs == req.link_libs

        # Modifying clone doesn't affect original
        clone.include_dirs.append(Path("other"))
        assert Path("other") not in req.include_dirs


class TestTarget:
    def test_creation(self):
        target = Target("mylib")
        assert target.name == "mylib"
        assert target.nodes == []
        assert target.sources == []
        assert target.dependencies == []

    def test_tracks_source_location(self):
        target = Target("mylib")
        assert target.defined_at is not None
        assert target.defined_at.lineno > 0

    def test_link_adds_dependency(self):
        lib1 = Target("lib1")
        lib2 = Target("lib2")
        app = Target("app")

        app.link(lib1)
        app.link(lib2)

        assert lib1 in app.dependencies
        assert lib2 in app.dependencies

    def test_link_avoids_duplicates(self):
        lib = Target("lib")
        app = Target("app")

        app.link(lib)
        app.link(lib)  # Same lib again

        assert app.dependencies.count(lib) == 1

    def test_usage_requirements(self):
        lib = Target("lib")
        lib.public.include_dirs.append(Path("include"))
        lib.public.defines.append("LIB_API")
        lib.private.defines.append("LIB_BUILDING")

        assert lib.public.include_dirs == [Path("include")]
        assert lib.public.defines == ["LIB_API"]
        assert lib.private.defines == ["LIB_BUILDING"]

    def test_collect_usage_requirements(self):
        """Test transitive requirement collection."""
        # Create a dependency chain: app -> libB -> libA
        libA = Target("libA")
        libA.public.include_dirs.append(Path("libA/include"))
        libA.public.defines.append("LIBA_API")

        libB = Target("libB")
        libB.public.include_dirs.append(Path("libB/include"))
        libB.link(libA)

        app = Target("app")
        app.private.defines.append("APP_PRIVATE")
        app.link(libB)

        requirements = app.collect_usage_requirements()

        # Should have app's private, plus libB and libA's public
        assert Path("libA/include") in requirements.include_dirs
        assert Path("libB/include") in requirements.include_dirs
        assert "LIBA_API" in requirements.defines
        assert "APP_PRIVATE" in requirements.defines

    def test_collect_usage_requirements_cached(self):
        """Test that collection is cached."""
        lib = Target("lib")
        app = Target("app")
        app.link(lib)

        req1 = app.collect_usage_requirements()
        req2 = app.collect_usage_requirements()

        assert req1 is req2  # Same object (cached)

    def test_collect_usage_requirements_invalidated(self):
        """Test that cache is invalidated on new link."""
        lib1 = Target("lib1")
        lib2 = Target("lib2")
        lib2.public.defines.append("LIB2")
        app = Target("app")
        app.link(lib1)

        req1 = app.collect_usage_requirements()
        assert "LIB2" not in req1.defines

        app.link(lib2)
        req2 = app.collect_usage_requirements()

        assert req2 is not req1
        assert "LIB2" in req2.defines

    def test_get_all_languages(self):
        lib = Target("lib")
        lib.required_languages.add("c")

        app = Target("app")
        app.required_languages.add("cxx")
        app.link(lib)

        langs = app.get_all_languages()
        assert "c" in langs
        assert "cxx" in langs

    def test_equality_by_name(self):
        t1 = Target("mylib")
        t2 = Target("mylib")
        t3 = Target("other")

        assert t1 == t2
        assert t1 != t3

    def test_hashable(self):
        t1 = Target("mylib")
        t2 = Target("mylib")

        targets = {t1, t2}
        assert len(targets) == 1  # Same name = same target


class TestImportedTarget:
    def test_creation(self):
        target = ImportedTarget("zlib", version="1.2.11")
        assert target.name == "zlib"
        assert target.is_imported is True
        assert target.package_name == "zlib"
        assert target.version == "1.2.11"

    def test_can_have_usage_requirements(self):
        target = ImportedTarget("zlib")
        target.public.include_dirs.append(Path("/usr/include"))
        target.public.link_libs.append("z")

        assert target.public.include_dirs == [Path("/usr/include")]
        assert target.public.link_libs == ["z"]

    def test_can_be_dependency(self):
        zlib = ImportedTarget("zlib")
        zlib.public.link_libs.append("z")

        app = Target("app")
        app.link(zlib)

        requirements = app.collect_usage_requirements()
        assert "z" in requirements.link_libs
