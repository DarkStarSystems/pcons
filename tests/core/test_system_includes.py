# SPDX-License-Identifier: MIT
"""System include directories (-isystem / /external:I / -imsvc)."""

from pathlib import Path

from pcons.core.project import Project
from pcons.core.target import UsageRequirements
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.build_context import (
    CompileLinkContext,
    MsvcCompileLinkContext,
)
from pcons.tools.requirements import (
    EffectiveRequirements,
    compute_effective_requirements,
)


class TestEffectiveRequirements:
    def test_merge_keeps_system_includes_separate(self):
        eff = EffectiveRequirements()
        reqs = UsageRequirements()
        reqs.include_dirs = [Path("include")]
        reqs.system_include_dirs = [Path("vendor/sdk")]

        eff.merge(reqs)

        assert eff.includes == [Path("include")]
        assert eff.system_includes == [Path("vendor/sdk")]

    def test_merge_deduplicates(self):
        eff = EffectiveRequirements()
        reqs = UsageRequirements()
        reqs.system_include_dirs = [Path("vendor"), Path("vendor")]

        eff.merge(reqs)
        eff.merge(reqs)

        assert eff.system_includes == [Path("vendor")]

    def test_participates_in_the_cache_key(self):
        plain = EffectiveRequirements(includes=[Path("a")])
        systemized = EffectiveRequirements(system_includes=[Path("a")])

        assert plain.as_hashable_tuple() != systemized.as_hashable_tuple()

    def test_clone_copies_them(self):
        eff = EffectiveRequirements(system_includes=[Path("vendor")])

        clone = eff.clone()
        clone.system_includes.append(Path("other"))

        assert eff.system_includes == [Path("vendor")]


class TestMakeIncludesSystem:
    def test_moves_include_dirs_over(self):
        reqs = UsageRequirements()
        reqs.include_dirs = [Path("vendor"), Path("vendor/detail")]

        reqs.make_includes_system()

        assert reqs.include_dirs == []
        assert reqs.system_include_dirs == [Path("vendor"), Path("vendor/detail")]

    def test_keeps_the_dirs_already_marked_system(self):
        reqs = UsageRequirements()
        reqs.include_dirs = [Path("a")]
        reqs.system_include_dirs = [Path("b")]

        reqs.make_includes_system()

        assert reqs.system_include_dirs == [Path("b"), Path("a")]

    def test_is_idempotent(self):
        reqs = UsageRequirements()
        reqs.include_dirs = [Path("vendor")]

        reqs.make_includes_system()
        reqs.make_includes_system()

        assert reqs.system_include_dirs == [Path("vendor")]

    def test_leaves_a_target_someone_else_created_usable(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        vendored = project.HeaderOnlyLibrary("vendored")
        vendored.public.include_dirs.append("vendor")
        app = project.Program("app", env, sources=[])
        app.link(vendored)

        vendored.public.make_includes_system()
        effective = compute_effective_requirements(app, env)

        assert Path("vendor") in effective.system_includes
        assert Path("vendor") not in effective.includes


class TestPropagation:
    def test_public_system_includes_reach_a_consumer(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        sdk = project.HeaderOnlyLibrary("sdk")
        sdk.public.system_include_dirs.append("vendor")
        app = project.Program("app", env, sources=[])
        app.link(sdk)

        effective = compute_effective_requirements(app, env)

        assert Path("vendor") in effective.system_includes
        assert Path("vendor") not in effective.includes

    def test_env_level_system_includes_are_picked_up(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.cc.system_includes.append("vendor")
        app = project.Program("app", env, sources=[])
        app.required_languages.add("c")

        effective = compute_effective_requirements(app, env)

        assert Path("vendor") in effective.system_includes


class TestContextRealization:
    def test_gnu_context_exposes_them_to_the_template(self):
        context = CompileLinkContext(system_includes=["vendor"], mode="compile")

        overrides = context.get_env_overrides()

        assert [p.path for p in overrides["system_includes"]] == ["vendor"]

    def test_msvc_adds_external_w0_only_when_there_are_any(self):
        with_sdk = MsvcCompileLinkContext(system_includes=["vendor"], mode="compile")
        without = MsvcCompileLinkContext(flags=["/W4"], mode="compile")

        assert "/external:W0" in with_sdk.get_env_overrides()["flags"]
        assert "/external:W0" not in (without.get_env_overrides().get("flags") or [])


class TestGeneratedCommand:
    def test_isystem_flag_is_relativized(self, tmp_path, gcc_toolchain):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.c").write_text("int main(void){return 0;}\n")
        (tmp_path / "vendor").mkdir()

        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.cc.system_includes.append(tmp_path / "vendor")
        project.Program("app", env, sources=["src/main.c"])

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "-isystem$topdir/vendor" in content
        assert f"-isystem{tmp_path}" not in content
