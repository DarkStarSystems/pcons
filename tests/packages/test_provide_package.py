# SPDX-License-Identifier: MIT
"""project.provide_package(): an in-tree target as the answer to find_package()."""

from __future__ import annotations

import pytest

from pcons.core.errors import PconsTypeError, PconsValueError
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.packages.finders.base import FinderChain
from pcons.packages.imported import ImportedTarget
from tests.packages.test_find_package import CountingMockFinder, MockFinder, _make_pkg


def _child(top: Project) -> Project:
    """A project included from *top*, as add_subdirectory() makes one."""
    with top._enter_subdir("sub"):
        return Project("child")


class TestProvidedAnswer:
    def test_it_answers_ahead_of_any_finder(self) -> None:
        project = Project("top")
        finder = CountingMockFinder({"zlib": _make_pkg("zlib")})
        project.add_package_finder(finder)
        zlib = Target("zlib-src")

        project.provide_package("zlib", zlib)

        assert project.find_package("zlib") is zlib
        assert finder.calls == 0

    def test_a_subdirectory_lookup_gets_it(self) -> None:
        top = Project("top")
        zlib = Target("zlib-src")
        top.provide_package("zlib", zlib)

        assert _child(top).find_package("zlib") is zlib

    def test_a_subdirectory_can_provide_it_to_the_whole_build(self) -> None:
        top = Project("top")
        zlib = Target("zlib-src", project=_child(top))
        zlib.project.provide_package("zlib", zlib)

        assert top.find_package("zlib") is zlib

    def test_components_and_system_do_not_apply(self) -> None:
        project = Project("top")
        boost = Target("boost-src")
        project.provide_package("boost", boost)

        found = project.find_package("boost", components=["filesystem"])

        assert found is boost
        assert project.find_package("boost", system=False) is boost
        assert not getattr(boost, "system", False)

    def test_it_takes_only_a_target(self) -> None:
        project = Project("top")
        with pytest.raises(PconsTypeError, match="takes a Target, not str"):
            project.provide_package("zlib", "zlib")  # ty: ignore[invalid-argument-type]


class TestVersion:
    def test_a_requirement_it_meets(self) -> None:
        project = Project("top")
        zlib = Target("zlib-src")
        project.provide_package("zlib", zlib, version="1.3.1")

        assert project.find_package("zlib", version=">=1.2") is zlib
        assert project.find_package("zlib", version="1.3.1") is zlib

    def test_a_requirement_it_fails_is_an_error(self) -> None:
        project = Project("top")
        project.provide_package("zlib", Target("zlib-src"), version="1.2.13")

        with pytest.raises(
            PconsValueError, match=r"provided by 'zlib-src' at version 1\.2\.13"
        ):
            project.find_package("zlib", version=">=1.3")

    def test_no_version_meets_every_requirement(self) -> None:
        project = Project("top")
        zlib = Target("zlib-src")
        project.provide_package("zlib", zlib)

        assert project.find_package("zlib", version=">=99") is zlib


class TestPerEnvironment:
    def test_an_environment_gets_its_own_ahead_of_the_shared_one(self) -> None:
        project = Project("top")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        shared, for_mcu = Target("zlib-src"), Target("zlib-mcu")

        project.provide_package("zlib", shared)
        project.provide_package("zlib", for_mcu, env=mcu)

        assert project.find_package("zlib", env=mcu) is for_mcu
        assert project.find_package("zlib", env=host) is shared

    def test_another_environment_still_searches_the_finders(self) -> None:
        project = Project("top")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        project.add_package_finder(MockFinder({"zlib": _make_pkg("zlib")}))
        for_mcu = Target("zlib-mcu")

        project.provide_package("zlib", for_mcu, env=mcu)

        assert isinstance(project.find_package("zlib", env=host), ImportedTarget)


class TestOrder:
    """A lookup before provide_package() got another answer: refused."""

    def test_a_package_already_found_is_an_error(self) -> None:
        project = Project("top")
        project.add_package_finder(MockFinder({"zlib": _make_pkg("zlib")}))
        project.find_package("zlib")

        with pytest.raises(
            PconsValueError, match=r"already ran and found ImportedTarget\('zlib'"
        ):
            project.provide_package("zlib", Target("zlib-src"))

    def test_providing_what_this_script_did_not_find_is_a_fallback(self) -> None:
        """Use the system copy if there is one, else build ours."""
        project = Project("top")
        project._package_finder_chains[None] = FinderChain([MockFinder()])
        zlib = project.find_package("zlib", required=False)
        if zlib is None:
            zlib = Target("zlib-src")
            project.provide_package("zlib", zlib)

        assert project.find_package("zlib") is zlib

    def test_another_script_that_found_nothing_counts(self) -> None:
        top = Project("top")
        top._package_finder_chains[None] = FinderChain([MockFinder()])
        assert _child(top).find_package("zlib", required=False) is None

        with pytest.raises(PconsValueError, match="already ran and found nothing"):
            top.provide_package("zlib", Target("zlib-src"))

    def test_a_lookup_in_a_subdirectory_counts(self) -> None:
        top = Project("top")
        child = _child(top)
        child.add_package_finder(MockFinder({"zlib": _make_pkg("zlib")}))
        child.find_package("zlib")

        with pytest.raises(PconsValueError, match="already ran"):
            top.provide_package("zlib", Target("zlib-src"))

    def test_a_lookup_in_another_environment_does_not(self) -> None:
        project = Project("top")
        mcu = project.Environment(name="mcu")
        host = project.Environment(name="host")
        project.add_package_finder(MockFinder({"zlib": _make_pkg("zlib")}))
        project.find_package("zlib", env=host)
        for_mcu = Target("zlib-mcu")

        project.provide_package("zlib", for_mcu, env=mcu)

        assert project.find_package("zlib", env=mcu) is for_mcu

    def test_an_environment_that_got_the_shared_one_cannot_switch(self) -> None:
        project = Project("top")
        mcu = project.Environment(name="mcu")
        project.provide_package("zlib", Target("zlib-src"))
        project.find_package("zlib", env=mcu)

        with pytest.raises(PconsValueError, match="in environment 'mcu'"):
            project.provide_package("zlib", Target("zlib-mcu"), env=mcu)

    def test_providing_the_same_target_again_is_harmless(self) -> None:
        project = Project("top")
        zlib = Target("zlib-src")
        project.provide_package("zlib", zlib)
        project.find_package("zlib")

        project.provide_package("zlib", zlib)

        assert project.find_package("zlib") is zlib

    def test_providing_another_target_is_an_error(self) -> None:
        project = Project("top")
        project.provide_package("zlib", Target("zlib-src"))

        with pytest.raises(PconsValueError, match="already provided by 'zlib-src'"):
            project.provide_package("zlib", Target("zlib-other"))
