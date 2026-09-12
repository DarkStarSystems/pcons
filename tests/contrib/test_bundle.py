# SPDX-License-Identifier: MIT
"""create_macos_bundle: resources under new names, PkgInfo from a file (#152)."""

from __future__ import annotations

from pathlib import Path

from pcons import Project
from pcons.contrib import bundle


def _plugin(project: Project, env):
    return env.Command(target="myplugin.so", source=None, command="true", name="plugin")


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
            "MyPlugin.bundle/Contents/Info.plist",
            "MyPlugin.bundle/Contents/MacOS/myplugin.so",
            "MyPlugin.bundle/Contents/PkgInfo",
            "MyPlugin.bundle/Contents/Resources/logo.png",
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

        outputs = {o for o in _outputs(project) if o.startswith("MyPlugin/")}
        assert outputs == {
            "MyPlugin/myplugin.so",
            "MyPlugin/helper.dll",
            "MyPlugin/art/logo.png",
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
