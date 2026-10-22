# SPDX-License-Identifier: MIT
"""System include directories (-isystem / /external:I / -imsvc)."""

import os
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.core.target import UsageRequirements
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.build_context import CompileLinkContext
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

    @pytest.mark.parametrize("system", [True, False])
    def test_msvc_gets_external_w0_only_when_there_are_any(self, tmp_path, system):
        """cl.exe ignores /external:I without an /external:W level (D9007)."""
        from pcons.packages.description import PackageDescription
        from pcons.packages.imported import ImportedTarget
        from pcons.toolchains import msvc

        toolchain = msvc.MsvcToolchain()
        toolchain._tools = {
            "cc": msvc.MsvcCompiler(),
            "cxx": msvc.MsvcCxxCompiler(),
            "lib": msvc.MsvcLibrarian(),
            "link": msvc.MsvcLinker(),
        }
        (tmp_path / "m.cpp").write_text("int main() { return 0; }\n")
        project = Project("t", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=toolchain)
        nanobind = ImportedTarget.from_package(
            PackageDescription(name="nb", include_dirs=["/opt/nb/include"]),
            system=system,
        )
        project.Program("app", env, sources=["m.cpp"]).link(nanobind)
        project.resolve()
        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)
        compile_rule = next(
            line
            for line in (tmp_path / "build" / "build.ninja").read_text().splitlines()
            if line.strip().startswith("command = cl.exe") and "/c " in line
        )

        assert ("/external:I" in compile_rule) is system
        assert ("/external:W0" in compile_rule) is system


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


class TestCompilersOwnDirectories:
    """A package's include dir the compiler already searches stays out of the
    system includes: -isystem would move it ahead in the search, and GCC's C++
    headers, which #include_next the C library's, would break."""

    def test_they_are_left_to_the_compiler(self, tmp_path, gcc_toolchain):
        from unittest.mock import patch

        from pcons.packages.description import PackageDescription
        from pcons.packages.imported import ImportedTarget

        (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")
        project = Project("t", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        zlib = ImportedTarget.from_package(
            PackageDescription(
                name="z", include_dirs=["/usr/include", "/opt/z/include"]
            )
        )
        app = project.Program("app", env, sources=["main.c"])
        app.link(zlib)

        with patch(
            "pcons.toolchains.unix._implicit_include_dirs",
            return_value=frozenset({os.path.realpath("/usr/include")}),
        ) as probe:
            eff = compute_effective_requirements(app, env, tool_name="cc")

        probe.assert_called_once_with(str(env.cc.cmd), "c")
        assert eff.system_includes == [Path("/opt/z/include")]
        assert Path("/usr/include") not in eff.includes

    def test_the_probe_reads_the_search_list(self):
        from unittest.mock import patch

        from pcons.toolchains.unix import _implicit_include_dirs

        stderr = (
            '#include "..." search starts here:\n'
            "#include <...> search starts here:\n"
            " /usr/lib/gcc/x86_64-linux-gnu/14/include\n"
            " /usr/include\n"
            " /Library/Frameworks (framework directory)\n"
            "End of search list.\n"
        )
        _implicit_include_dirs.cache_clear()
        with patch("pcons.toolchains.unix.subprocess.run") as run:
            run.return_value.stderr = stderr
            dirs = _implicit_include_dirs("gcc-x", "c")
        _implicit_include_dirs.cache_clear()
        assert os.path.realpath("/usr/include") in dirs
        assert any(Path(d).as_posix().endswith("/Library/Frameworks") for d in dirs)
