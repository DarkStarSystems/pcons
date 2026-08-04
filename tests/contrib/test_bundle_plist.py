# SPDX-License-Identifier: MIT
"""Tests for pcons.contrib.bundle: Info.plist generation and installation.

Covers two fixes:
- generate_info_plist() must XML-escape interpolated values so the result
  is well-formed XML even when names/versions contain &, <, >, or ".
- create_macos_bundle() must actually write and install an Info.plist when
  given a string (the documented usage), not silently drop it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from pcons import Project
from pcons.contrib import bundle


class TestGenerateInfoPlistEscaping:
    """generate_info_plist() must produce well-formed, parseable XML."""

    def test_special_characters_produce_well_formed_xml(self) -> None:
        plist = bundle.generate_info_plist(
            name='Foo & Bar <"Baz">',
            version="1.0 & 2",
            identifier="com.example.foo&bar",
            executable='Foo & "Bar"',
            extra_keys={"MyKey & <Weird>": 'Value with "quotes" & <tags>'},
        )

        # Must parse without raising ET.ParseError.
        root = ET.fromstring(plist)
        assert root.tag == "plist"

        # The raw (unescaped) special characters must not appear verbatim.
        assert "Foo & Bar" not in plist
        assert "<Weird>" not in plist

        # And the escaped values must round-trip through parsing intact.
        strings = [el.text for el in root.iter("string")]
        assert 'Foo & Bar <"Baz">' in strings
        assert 'Foo & "Bar"' in strings
        assert 'Value with "quotes" & <tags>' in strings

    def test_plain_values_unaffected(self) -> None:
        plist = bundle.generate_info_plist("MyPlugin", "1.0.0", bundle_type="BNDL")
        root = ET.fromstring(plist)
        strings = [el.text for el in root.iter("string")]
        assert "MyPlugin" in strings
        assert "1.0.0" in strings
        assert "BNDL" in strings


class TestCreateMacosBundleStringPlist:
    """create_macos_bundle() with a string info_plist must install it."""

    def _make_plugin(self, project: Project, env):
        # A trivial Target standing in for a compiled plugin binary; the
        # bundle graph only cares that it's a Target with an output node.
        return env.Command(
            target="myplugin.so",
            source=None,
            command="true",
            name="plugin",
        )

    def test_string_info_plist_creates_info_plist_install_target(
        self, tmp_path: Path
    ) -> None:
        project = Project("test_bundle", build_dir=tmp_path / "build")
        env = project.Environment()
        plugin = self._make_plugin(project, env)

        plist_content = bundle.generate_info_plist("MyPlugin", "1.0.0")

        bundle.create_macos_bundle(
            project,
            env,
            plugin,
            bundle_dir="MyPlugin.bundle",
            info_plist=plist_content,
        )

        project.resolve()

        # Find every output node produced by any target and confirm an
        # Info.plist ends up somewhere under the bundle's Contents dir.
        plist_nodes = [
            node
            for target in project.targets
            for node in target.output_nodes
            if node.path.name == "Info.plist"
        ]
        assert plist_nodes, "expected an Info.plist output node in the bundle graph"
        assert any(node.path.parent.name == "Contents" for node in plist_nodes), (
            f"Info.plist not installed under Contents/: {[n.path for n in plist_nodes]}"
        )

    def test_path_info_plist_still_installs(self, tmp_path: Path) -> None:
        """Regression guard: the pre-existing Path branch keeps working."""
        project = Project("test_bundle_path", build_dir=tmp_path / "build")
        env = project.Environment()
        plugin = self._make_plugin(project, env)

        existing_plist = tmp_path / "Info.plist"
        existing_plist.write_text(bundle.generate_info_plist("MyPlugin", "1.0.0"))

        bundle.create_macos_bundle(
            project,
            env,
            plugin,
            bundle_dir="MyPlugin.bundle",
            info_plist=existing_plist,
        )

        project.resolve()

        plist_nodes = [
            node
            for target in project.targets
            for node in target.output_nodes
            if node.path.name == "Info.plist"
        ]
        assert plist_nodes


class TestPkgInfoAndConfigureTimeWriting:
    """Bundle files whose content the script computes are written during
    generate, so a bundle costs no rule and no process of its own."""

    def _make_plugin(self, project, env):
        return env.Command(
            target="myplugin.so", source=None, command="true", name="plugin"
        )

    def test_pkginfo_is_written_byte_exact(self, tmp_path: Path) -> None:
        """A classic PkgInfo is 8 bytes with no trailing newline."""
        project = Project("pkginfo", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            self._make_plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            pkginfo=b"eFKTFXTC",
        )

        written = tmp_path / "build" / ".bundle_staging" / "MyPlugin.bundle" / "PkgInfo"
        assert written.read_bytes() == b"eFKTFXTC"

    def test_pkginfo_is_installed_into_contents(self, tmp_path: Path) -> None:
        project = Project("pkginfo_install", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            self._make_plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            info_plist=bundle.generate_info_plist("MyPlugin", "1.0"),
            pkginfo="BNDL????",
        )
        project.resolve()

        installed = {
            node.path.name
            for target in project.targets
            for node in target.output_nodes
            if node.path.parent.name == "Contents"
        }
        assert {"Info.plist", "PkgInfo"} <= installed

    def test_the_plist_text_stays_out_of_the_build_graph(self, tmp_path: Path) -> None:
        """The content is known at configure time, so no edge carries it."""
        project = Project("no_edge", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            self._make_plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            info_plist=bundle.generate_info_plist("MyPlugin", "1.0"),
        )
        project.resolve()

        commands = [
            str(node._build_info.get("command"))
            for target in project.targets
            for node in target.output_nodes
            if getattr(node, "_build_info", None)
        ]
        assert not any("CFBundleName" in c for c in commands)

    def test_staging_stays_under_the_build_dir(self, tmp_path: Path) -> None:
        """Not in the source tree: write_file resolves a relative path
        against the working directory, which at configure time is the root."""
        project = Project("staging", root_dir=tmp_path, build_dir="build")
        env = project.Environment()

        bundle.create_macos_bundle(
            project,
            env,
            self._make_plugin(project, env),
            bundle_dir="MyPlugin.bundle",
            pkginfo=b"BNDL????",
        )

        assert not (tmp_path / ".bundle_staging").exists()
        assert (tmp_path / "build" / ".bundle_staging").is_dir()
