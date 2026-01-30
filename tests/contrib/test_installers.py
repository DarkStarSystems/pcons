# SPDX-License-Identifier: MIT
"""Tests for installer generation modules."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pcons import Project
from pcons.contrib.installers import _helpers


class TestHelpers:
    """Tests for installer helper functions."""

    def test_generate_component_plist(self, tmp_path: Path) -> None:
        """Test component plist generation."""
        output = tmp_path / "component.plist"
        _helpers.generate_component_plist(output)

        assert output.exists()
        content = output.read_text()
        # Check for plist format
        assert "plist" in content
        assert "BundleIsRelocatable" in content or "dict" in content

    def test_generate_component_plist_custom(self, tmp_path: Path) -> None:
        """Test component plist with custom settings."""
        output = tmp_path / "component.plist"
        _helpers.generate_component_plist(
            output,
            relocatable=True,
            version_checked=False,
            overwrite_action="update",
        )

        assert output.exists()

    def test_generate_distribution_xml(self, tmp_path: Path) -> None:
        """Test distribution.xml generation."""
        output = tmp_path / "distribution.xml"
        _helpers.generate_distribution_xml(
            output,
            title="Test App",
            identifier="com.test.app",
            version="1.0.0",
            packages=["TestApp.pkg"],
        )

        assert output.exists()
        content = output.read_text()
        assert "installer-gui-script" in content
        assert "Test App" in content
        assert "com.test.app" in content
        assert "1.0.0" in content

    def test_generate_distribution_xml_with_min_os(self, tmp_path: Path) -> None:
        """Test distribution.xml with minimum OS version."""
        output = tmp_path / "distribution.xml"
        _helpers.generate_distribution_xml(
            output,
            title="Test App",
            identifier="com.test.app",
            version="1.0.0",
            packages=["TestApp.pkg"],
            min_os_version="10.13",
        )

        content = output.read_text()
        assert "os-version" in content
        assert "10.13" in content

    def test_generate_appx_manifest(self, tmp_path: Path) -> None:
        """Test AppxManifest.xml generation."""
        output = tmp_path / "AppxManifest.xml"
        _helpers.generate_appx_manifest(
            output,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
        )

        assert output.exists()
        content = output.read_text()
        assert "Package" in content
        assert "TestApp" in content
        assert "CN=Test Publisher" in content
        # Version should have 4 components
        assert "1.0.0.0" in content

    def test_check_tool_found(self) -> None:
        """Test that check_tool finds common tools."""
        # Use a tool that exists on all platforms
        # On Windows it's "python", on Unix it's "python3"
        tool = "python" if sys.platform == "win32" else "python3"
        path = _helpers.check_tool(tool)
        assert path is not None

    def test_check_tool_not_found(self) -> None:
        """Test that check_tool raises for missing tools."""
        with pytest.raises(_helpers.ToolNotFoundError) as exc_info:
            _helpers.check_tool("definitely_not_a_real_tool_xyz123")
        assert "not found" in str(exc_info.value)

    def test_check_tool_with_hint(self) -> None:
        """Test that check_tool includes hint in error."""
        with pytest.raises(_helpers.ToolNotFoundError) as exc_info:
            _helpers.check_tool("not_real", hint="Try installing it")
        assert "Try installing it" in str(exc_info.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only tests")
class TestMacOSInstallers:
    """Tests for macOS installer creation (macOS only)."""

    def test_create_dmg_basic(self, tmp_path: Path) -> None:
        """Test basic DMG creation setup."""
        from pcons.contrib.installers import macos

        # Create a simple test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")

        # Create project
        project = Project("test_dmg", build_dir=tmp_path / "build")
        env = project.Environment()

        # Create DMG target
        dmg = macos.create_dmg(
            project,
            env,
            name="TestApp",
            sources=[test_file],
        )

        # Verify target was created
        assert dmg is not None
        assert dmg.name == "dmg_TestApp"

    def test_create_pkg_basic(self, tmp_path: Path) -> None:
        """Test basic PKG creation setup."""
        from pcons.contrib.installers import macos

        # Create a simple test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")

        # Create project
        project = Project("test_pkg", build_dir=tmp_path / "build")
        env = project.Environment()

        # Create PKG target
        pkg = macos.create_pkg(
            project,
            env,
            name="TestApp",
            version="1.0.0",
            identifier="com.test.app",
            sources=[test_file],
            install_location="/usr/local/bin",
        )

        # Verify target was created
        assert pkg is not None
        assert pkg.name == "pkg_TestApp"

    def test_create_component_pkg_basic(self, tmp_path: Path) -> None:
        """Test basic component PKG creation setup."""
        from pcons.contrib.installers import macos

        # Create a simple test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")

        # Create project
        project = Project("test_component_pkg", build_dir=tmp_path / "build")
        env = project.Environment()

        # Create component PKG target
        pkg = macos.create_component_pkg(
            project,
            env,
            identifier="com.test.app",
            version="1.0.0",
            sources=[test_file],
            install_location="/usr/local/bin",
        )

        # Verify target was created
        assert pkg is not None
        assert "com_test_app" in pkg.name

    def test_create_pkg_with_directory_source(self, tmp_path: Path) -> None:
        """Test PKG creation with a directory source (auto-detected)."""
        from pcons.contrib.installers import macos

        # Create a bundle directory
        bundle_dir = tmp_path / "MyApp.bundle"
        bundle_dir.mkdir()
        (bundle_dir / "Contents").mkdir()
        (bundle_dir / "Contents" / "Info.plist").write_text("<plist/>")

        # Create project
        project = Project("test_pkg_dirs", build_dir=tmp_path / "build")
        env = project.Environment()

        # Pass directory as a regular source — Install auto-detects it
        pkg = macos.create_pkg(
            project,
            env,
            name="TestApp",
            version="1.0.0",
            identifier="com.test.app",
            sources=[bundle_dir],
            install_location="/Library/Bundles",
        )

        assert pkg is not None
        assert pkg.name == "pkg_TestApp"

    def test_sign_pkg_command(self, tmp_path: Path) -> None:
        """Test that sign_pkg returns correct command."""
        from pcons.contrib.installers import macos

        pkg_path = tmp_path / "test.pkg"
        cmd = macos.sign_pkg(pkg_path, "Developer ID Installer: Test")

        assert cmd[0] == "productsign"
        assert "--sign" in cmd
        assert "Developer ID Installer: Test" in cmd
        assert str(pkg_path) in cmd

    def test_notarize_cmd_with_keychain(self, tmp_path: Path) -> None:
        """Test notarize command with keychain profile."""
        from pcons.contrib.installers import macos

        pkg_path = tmp_path / "test.pkg"
        cmd = macos.notarize_cmd(
            pkg_path,
            apple_id="test@example.com",
            team_id="TEAM123",
            password_keychain_item="my-profile",
        )

        assert "bash" in cmd[0]
        assert "notarytool" in cmd[-1]
        assert "my-profile" in cmd[-1]
        assert "stapler" in cmd[-1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tests")
class TestWindowsInstallers:
    """Tests for Windows installer creation (Windows only)."""

    def test_create_msix_basic(self, tmp_path: Path) -> None:
        """Test basic MSIX creation setup."""
        from pcons.contrib.installers import windows

        # Create a simple test file
        test_file = tmp_path / "test.exe"
        test_file.write_text("dummy exe")

        # Create project
        project = Project("test_msix", build_dir=tmp_path / "build")
        env = project.Environment()

        # Create MSIX target
        msix = windows.create_msix(
            project,
            env,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
            sources=[test_file],
        )

        # Verify target was created
        assert msix is not None
        assert msix.name == "msix_TestApp"

    def test_create_msix_with_options(self, tmp_path: Path) -> None:
        """Test MSIX creation with display name and description."""
        from pcons.contrib.installers import windows

        test_file = tmp_path / "test.exe"
        test_file.write_text("dummy exe")

        project = Project("test_msix_opts", build_dir=tmp_path / "build")
        env = project.Environment()

        msix = windows.create_msix(
            project,
            env,
            name="TestApp",
            version="1.0.0.0",
            publisher="CN=Test Publisher",
            sources=[test_file],
            display_name="Test Application",
            description="A test application",
        )

        assert msix is not None

    def test_find_sdk_tool(self) -> None:
        """Test that _find_sdk_tool can find MakeAppx.exe."""
        from pcons.contrib.installers import windows

        path = windows._find_sdk_tool("MakeAppx.exe")
        # May or may not be found depending on SDK installation
        if path:
            assert "MakeAppx" in path


class TestInstallersCLI:
    """Tests for the _helpers CLI interface."""

    def test_cli_gen_plist(self, tmp_path: Path) -> None:
        """Test CLI plist generation."""
        import subprocess

        output = tmp_path / "test.plist"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_plist",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert output.exists()

    def test_cli_gen_distribution(self, tmp_path: Path) -> None:
        """Test CLI distribution.xml generation."""
        import subprocess

        output = tmp_path / "distribution.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_distribution",
                "--output",
                str(output),
                "--title",
                "Test App",
                "--identifier",
                "com.test.app",
                "--version",
                "1.0.0",
                "--package",
                "test.pkg",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert output.exists()

    def test_cli_gen_appx_manifest(self, tmp_path: Path) -> None:
        """Test CLI AppxManifest.xml generation."""
        import subprocess

        output = tmp_path / "AppxManifest.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcons.contrib.installers._helpers",
                "gen_appx_manifest",
                "--output",
                str(output),
                "--name",
                "TestApp",
                "--version",
                "1.0.0",
                "--publisher",
                "CN=Test",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert output.exists()
