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
