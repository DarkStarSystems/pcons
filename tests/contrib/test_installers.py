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


class TestMsixSigning:
    """Tests for MSIX signing target correctness (platform-independent).

    These only exercise the build-graph construction in windows.create_msix,
    not the real MakeAppx.exe/SignTool.exe tools, so they run on any OS.
    """

    @staticmethod
    def _mock_find_sdk_tool(tool_name: str) -> str:
        if tool_name == "MakeAppx.exe":
            return "/fake/MakeAppx.exe"
        if tool_name == "SignTool.exe":
            return "/fake/SignTool.exe"
        raise AssertionError(f"unexpected tool lookup: {tool_name}")

    def test_signed_target_output_matches_produced_file(self, monkeypatch) -> None:
        """The declared ninja target must be the file the command actually writes.

        Regression test: previously the signed target was declared as
        output.with_suffix(".signed.msix") while the sign command modified
        the unsigned output in place, so the declared target was never
        produced (a perpetually-dirty, phantom output).
        """
        from pcons.contrib.installers import windows

        monkeypatch.setattr(windows, "_find_sdk_tool", self._mock_find_sdk_tool)

        project = Project("test_msix_sign_target")
        env = project.Environment()

        signed = windows.create_msix(
            project=project,
            env=env,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
            sources=[],
            sign_cert=Path("dummy_cert.pfx"),
        )

        assert len(signed.output_nodes) == 1
        declared_path = signed.output_nodes[0].path
        assert declared_path == Path("TestApp-1.0.0.signed.msix")

        # The command's own output file (the last --output value) must be
        # the same path as the declared ninja target.
        command = signed.output_nodes[0]._build_info["command"]
        assert command[command.index("--output") + 1] == str(declared_path)

    def test_signed_target_input_is_unsigned_output(self, monkeypatch) -> None:
        """The sign step must consume the unsigned .msix, not itself."""
        from pcons.contrib.installers import windows

        monkeypatch.setattr(windows, "_find_sdk_tool", self._mock_find_sdk_tool)

        project = Project("test_msix_sign_input")
        env = project.Environment()

        signed = windows.create_msix(
            project=project,
            env=env,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
            sources=[],
            sign_cert=Path("dummy_cert.pfx"),
        )

        command = signed.output_nodes[0]._build_info["command"]
        assert command[command.index("--input") + 1] == "TestApp-1.0.0.msix"

    def test_signing_password_not_embedded_literally(self, monkeypatch) -> None:
        """The certificate password must never appear literally in the command.

        Only the *name* of an environment variable may appear; the value is
        resolved from the environment when the signing step actually runs.
        """
        from pcons.contrib.installers import windows

        monkeypatch.setattr(windows, "_find_sdk_tool", self._mock_find_sdk_tool)

        project = Project("test_msix_sign_password")
        env = project.Environment()

        secret = "hunter2-super-secret-password"  # noqa: S105 - test value
        signed = windows.create_msix(
            project=project,
            env=env,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
            sources=[],
            sign_cert=Path("dummy_cert.pfx"),
            sign_password_env="PCONS_TEST_MSIX_PASSWORD",
        )

        command = signed.output_nodes[0]._build_info["command"]
        assert secret not in command
        assert "PCONS_TEST_MSIX_PASSWORD" in command
        assert command[command.index("--password-env") + 1] == (
            "PCONS_TEST_MSIX_PASSWORD"
        )

    def test_signing_without_password_env_omits_flag(self, monkeypatch) -> None:
        """No --password-env token should appear when no password is needed."""
        from pcons.contrib.installers import windows

        monkeypatch.setattr(windows, "_find_sdk_tool", self._mock_find_sdk_tool)

        project = Project("test_msix_sign_no_password")
        env = project.Environment()

        signed = windows.create_msix(
            project=project,
            env=env,
            name="TestApp",
            version="1.0.0",
            publisher="CN=Test Publisher",
            sources=[],
            sign_cert=Path("dummy_cert.pfx"),
        )

        command = signed.output_nodes[0]._build_info["command"]
        assert "--password-env" not in command


class TestSignMsixHelper:
    """Tests for the sign_msix _helpers function that performs the copy+sign."""

    def test_copies_and_signs(self, tmp_path: Path, monkeypatch) -> None:
        from pcons.contrib.installers import _helpers

        input_path = tmp_path / "unsigned.msix"
        output_path = tmp_path / "signed.msix"
        input_path.write_text("fake msix contents")

        calls: list[list[str]] = []

        def fake_run(args: list[str], check: bool) -> None:  # noqa: ARG001
            calls.append(args)

        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)

        _helpers.sign_msix(
            input_path,
            output_path,
            signtool="/fake/SignTool.exe",
            cert=tmp_path / "cert.pfx",
        )

        # Original left untouched; copy created at the declared output path.
        assert input_path.exists()
        assert output_path.read_text() == "fake msix contents"
        assert len(calls) == 1
        assert calls[0][0] == "/fake/SignTool.exe"
        assert str(output_path) in calls[0]

    def test_password_env_resolved_at_runtime(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons.contrib.installers import _helpers

        input_path = tmp_path / "unsigned.msix"
        output_path = tmp_path / "signed.msix"
        input_path.write_text("fake msix contents")

        monkeypatch.setenv("PCONS_TEST_SIGN_PW", "hunter2-super-secret-password")

        calls: list[list[str]] = []
        monkeypatch.setattr(
            _helpers.subprocess,
            "run",
            lambda args, check: calls.append(args),  # noqa: ARG005
        )

        _helpers.sign_msix(
            input_path,
            output_path,
            signtool="/fake/SignTool.exe",
            cert=tmp_path / "cert.pfx",
            password_env="PCONS_TEST_SIGN_PW",
        )

        assert "hunter2-super-secret-password" in calls[0]

    def test_missing_password_env_raises(self, tmp_path: Path, monkeypatch) -> None:
        from pcons.contrib.installers import _helpers

        input_path = tmp_path / "unsigned.msix"
        input_path.write_text("fake msix contents")
        monkeypatch.delenv("PCONS_TEST_SIGN_PW_UNSET", raising=False)

        with pytest.raises(_helpers.InstallerError, match="PCONS_TEST_SIGN_PW_UNSET"):
            _helpers.sign_msix(
                input_path,
                tmp_path / "signed.msix",
                signtool="/fake/SignTool.exe",
                cert=tmp_path / "cert.pfx",
                password_env="PCONS_TEST_SIGN_PW_UNSET",
            )


class TestValidateStagingPath:
    """Tests for macos._validate_staging_path conflict detection.

    Platform-independent: this only walks in-memory project/target state,
    it never shells out to pkgbuild/hdiutil.
    """

    def test_raises_on_target_output_conflict(self, tmp_path: Path) -> None:
        from pcons.contrib.installers import macos

        project = Project("test_staging_conflict", build_dir=tmp_path / "build")
        env = project.Environment()

        # A target whose output lives under the staging directory. Normal
        # builder outputs (Program/Library/...) carry the build_dir prefix
        # on their node path, so model that here rather than a bare
        # build_dir-relative path.
        env.Command(
            target=project.build_dir
            / ".pkg_staging"
            / "MyApp"
            / "payload"
            / "leftover.txt",
            source=None,
            command="",
            name="conflicting_target",
        )

        with pytest.raises(ValueError, match="conflicts with"):
            macos._validate_staging_path(project, ".pkg_staging")

    def test_raises_on_raw_node_conflict(self, tmp_path: Path) -> None:
        from pcons.contrib.installers import macos

        project = Project("test_staging_node_conflict", build_dir=tmp_path / "build")

        # A raw node (not attached to any Target) directly under staging.
        project.node(project.build_dir / ".pkg_staging" / "stray_file.txt")

        with pytest.raises(ValueError, match="conflicts with"):
            macos._validate_staging_path(project, ".pkg_staging")

    def test_no_conflict_for_unrelated_outputs(self, tmp_path: Path) -> None:
        from pcons.contrib.installers import macos

        project = Project("test_staging_no_conflict", build_dir=tmp_path / "build")
        env = project.Environment()

        # Unrelated output, not under the staging directory.
        env.Command(
            target=Path("dist") / "output.txt",
            source=None,
            command="",
            name="unrelated_target",
        )
        project.node(project.build_dir / "other" / "file.txt")

        # Should not raise.
        macos._validate_staging_path(project, ".pkg_staging")

    def test_no_conflict_for_sibling_prefix(self, tmp_path: Path) -> None:
        """A staging dir with a matching prefix but different name isn't a conflict."""
        from pcons.contrib.installers import macos

        project = Project("test_staging_prefix", build_dir=tmp_path / "build")
        env = project.Environment()

        # ".pkg_staging_extra" is not under ".pkg_staging" despite the string
        # prefix match, so this must not raise.
        env.Command(
            target=project.build_dir / ".pkg_staging_extra" / "file.txt",
            source=None,
            command="",
            name="sibling_target",
        )

        macos._validate_staging_path(project, ".pkg_staging")


class TestWindowsInstallersErrors:
    """Tests for error handling in Windows installer creation."""

    def test_create_msix_without_makeappx(self, monkeypatch) -> None:
        """Test that _find_sdk_tool raises if tool is not found."""
        from pcons.contrib.installers import windows
        from pcons.contrib.installers._helpers import ToolNotFoundError

        # Monkeypatch _find_sdk_tool to always return None
        def _mock_find_sdk_tool(tool_name: str) -> str | None:
            if tool_name == "MakeAppx.exe":
                return None
            else:
                return "sometool.exe"

        monkeypatch.setattr(windows, "_find_sdk_tool", _mock_find_sdk_tool)
        project = Project("test_msix_error")
        env = project.Environment()
        with pytest.raises(ToolNotFoundError, match="MakeAppx.exe"):
            windows.create_msix(
                project=project,
                env=env,
                name="TestApp",
                version="1.0.0",
                publisher="CN=Test Publisher",
                sources=[],
            )

    def test_create_msix_with_signing_but_no_signtool(self, monkeypatch) -> None:
        """Test that _find_sdk_tool raises if SignTool.exe is not found when signing."""
        from pcons.contrib.installers import windows
        from pcons.contrib.installers._helpers import ToolNotFoundError

        # Monkeypatch _find_sdk_tool to return None for SignTool.exe
        def _mock_find_sdk_tool(tool_name: str) -> str | None:
            if tool_name == "SignTool.exe":
                return None
            else:
                return "sometool.exe"

        monkeypatch.setattr(windows, "_find_sdk_tool", _mock_find_sdk_tool)
        project = Project("test_msix_sign_error")
        env = project.Environment()
        with pytest.raises(ToolNotFoundError, match="SignTool.exe"):
            windows.create_msix(
                project=project,
                env=env,
                name="TestApp",
                version="1.0.0",
                publisher="CN=Test Publisher",
                sources=[],
                sign_cert=Path("dummy_cert.pfx"),
            )


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
