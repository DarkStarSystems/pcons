# SPDX-License-Identifier: MIT
"""create_macos_bundle: resources under new names, PkgInfo from a file (#152)."""

from __future__ import annotations

from pathlib import Path

from pcons import Generator, Project
from pcons.contrib import bundle
from pcons.generators.generator import BaseGenerator


def _plugin(project: Project, env):
    return env.Command(target="myplugin.so", source=None, command="true")


def _outputs(project: Project) -> set[str]:
    project.resolve()
    return {
        node.path.as_posix()
        for target in project.targets
        for node in target.output_nodes
    }


class TestBundleIsABuildProduct:
    """bundle_dir is relative to the build directory, not the install prefix:
    the bundle is what installers stage from, not an install itself."""

    def test_macos_bundle_lands_in_the_build_dir(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        (tmp_path / "PkgInfo").write_bytes(b"BNDL????")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            info_plist="<plist/>",
            pkginfo=Path(tmp_path / "PkgInfo"),
            resources=["logo.png"],
        )

        outputs = {o for o in _outputs(project) if "MyPlugin.bundle" in o}
        assert outputs == {
            "build/MyPlugin.bundle/Contents/Info.plist",
            "build/MyPlugin.bundle/Contents/MacOS/myplugin.so",
            "build/MyPlugin.bundle/Contents/PkgInfo",
            "build/MyPlugin.bundle/Contents/Resources/logo.png",
        }

    def test_flat_bundle_lands_in_the_build_dir(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        (tmp_path / "helper.dll").write_bytes(b"dll")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_flat_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin",
            dlls=["helper.dll"],
            resources={"art/logo.png": "logo.png"},
        )

        outputs = {o for o in _outputs(project) if o.startswith("build/MyPlugin/")}
        assert outputs == {
            "build/MyPlugin/myplugin.so",
            "build/MyPlugin/helper.dll",
            "build/MyPlugin/art/logo.png",
        }
        assert not any("dist/" in o for o in _outputs(project))


class TestResourcesMapping:
    def test_a_mapping_renames_on_the_way_in(self, tmp_path: Path) -> None:
        (tmp_path / "logo-white.png").write_bytes(b"png")
        (tmp_path / "logo.png").write_bytes(b"png")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            resources={
                "com.example.MyPlugin.png": "logo-white.png",
                "logo.png": "logo.png",
            },
        )

        outputs = _outputs(project)
        assert any(
            o.endswith("Contents/Resources/com.example.MyPlugin.png") for o in outputs
        )
        assert any(o.endswith("Contents/Resources/logo.png") for o in outputs)
        assert not any(o.endswith("Resources/logo-white.png") for o in outputs)

    def test_a_list_keeps_names(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="P.bundle",
            resources=["logo.png"],
        )

        assert any(o.endswith("Contents/Resources/logo.png") for o in _outputs(project))

    def test_flat_bundle_takes_a_mapping_too(self, tmp_path: Path) -> None:
        (tmp_path / "logo-white.png").write_bytes(b"png")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_flat_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin",
            resources={"icon.png": "logo-white.png"},
        )

        assert any(o.endswith("MyPlugin/icon.png") for o in _outputs(project))


class TestPkgInfoFromAFile:
    def test_a_path_is_copied_in_as_pkginfo(self, tmp_path: Path) -> None:
        (tmp_path / "plugin-pkg.info").write_bytes(b"eFKTFXTC")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            pkginfo=tmp_path / "plugin-pkg.info",
        )

        outputs = _outputs(project)
        assert any(o.endswith("MyPlugin.bundle/Contents/PkgInfo") for o in outputs)
        assert not (
            tmp_path / "build" / ".bundle_staging" / "MyPlugin.bundle" / "PkgInfo"
        ).exists()


class TestTheReturnedTargetStandsForTheBundle:
    def test_it_depends_on_the_other_installs(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        (tmp_path / "plugin-pkg.info").write_bytes(b"BNDL????")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()

        installed = bundle.create_macos_bundle(
            project,
            env,
            _plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            info_plist=bundle.generate_info_plist("MyPlugin", "1.0.0"),
            pkginfo=tmp_path / "plugin-pkg.info",
            resources=["logo.png"],
        )

        project.resolve()
        covered = {
            node.path.name
            for dep in installed.dependencies
            for node in dep.output_nodes
        }
        assert {"Info.plist", "PkgInfo", "logo.png"} <= covered


def _plugin_edge(ninja: str, out: str) -> str:
    """The one build statement in *ninja* producing *out*."""
    lines = [line for line in ninja.splitlines() if line.startswith(f"build {out}:")]
    assert len(lines) == 1, lines
    return lines[0]


class TestTheBundlePartsOnlyOrderTheCopy:
    """The parts belong to the bundle, but none of them is an input to the
    binary's copy: editing a resource must not recopy the binary. That is
    order-only in ninja -- after ``||``, not ``|``."""

    def _ninja(self, tmp_path: Path, make_bundle) -> str:
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment()
        installed = make_bundle(project, env)
        project.Default(installed)
        Generator().generate(project)
        BaseGenerator._generate_pending(project)
        return (tmp_path / "build" / "build.ninja").read_text()

    def test_macos_bundle_orders_the_parts_after_the_copy(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        ninja = self._ninja(
            tmp_path,
            lambda project, env: bundle.create_macos_bundle(
                project,
                env,
                _plugin(project, env),
                bundle_dir="MyPlugin.bundle",
                info_plist="<plist/>",
                pkginfo=b"BNDL????",
                resources=["logo.png"],
            ),
        )

        edge = _plugin_edge(ninja, "MyPlugin.bundle/Contents/MacOS/myplugin.so")
        inputs, _, order_only = edge.partition("||")
        assert "|" not in inputs
        for part in ("Contents/Info.plist", "Contents/PkgInfo", "Resources/logo.png"):
            assert part in order_only

        # Order-only deps are still built, so Default(bundle) covers the
        # whole bundle: every part is reachable from the default target.
        assert "default MyPlugin.bundle/Contents/MacOS/myplugin.so" in ninja

    def test_flat_bundle_orders_the_parts_after_the_copy(self, tmp_path: Path) -> None:
        (tmp_path / "logo.png").write_bytes(b"png")
        (tmp_path / "helper.dll").write_bytes(b"dll")
        ninja = self._ninja(
            tmp_path,
            lambda project, env: bundle.create_flat_bundle(
                project,
                env,
                _plugin(project, env),
                bundle_dir="MyPlugin",
                dlls=[tmp_path / "helper.dll"],
                resources=["logo.png"],
            ),
        )

        edge = _plugin_edge(ninja, "MyPlugin/myplugin.so")
        inputs, _, order_only = edge.partition("||")
        assert "|" not in inputs
        assert "MyPlugin/helper.dll" in order_only
        assert "MyPlugin/logo.png" in order_only
