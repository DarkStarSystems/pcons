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
