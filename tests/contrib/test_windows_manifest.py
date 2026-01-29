# SPDX-License-Identifier: MIT
"""Tests for pcons.contrib.windows.manifest."""

from pcons.contrib.windows.manifest import (
    _create_assembly_manifest_xml,
    _create_manifest_xml,
)


class TestCreateManifestXml:
    """Tests for the _create_manifest_xml function."""

    def test_basic_manifest(self):
        """Test generating a basic manifest with no options."""
        xml = _create_manifest_xml()
        assert '<?xml version="1.0" encoding="UTF-8"' in xml
        assert "urn:schemas-microsoft-com:asm.v1" in xml
        assert 'manifestVersion="1.0"' in xml

    def test_dpi_aware_true(self):
        """Test DPI awareness set to True."""
        xml = _create_manifest_xml(dpi_aware=True)
        assert "dpiAware" in xml
        assert ">true<" in xml

    def test_dpi_aware_system(self):
        """Test DPI awareness set to 'system'."""
        xml = _create_manifest_xml(dpi_aware="system")
        assert "dpiAware" in xml
        assert ">true<" in xml

    def test_dpi_aware_permonitor(self):
        """Test DPI awareness set to PerMonitor."""
        xml = _create_manifest_xml(dpi_aware="PerMonitor")
        assert "dpiAware" in xml
        assert ">true/pm<" in xml

    def test_dpi_aware_permonitorv2(self):
        """Test DPI awareness set to PerMonitorV2."""
        xml = _create_manifest_xml(dpi_aware="PerMonitorV2")
        assert "dpiAware" in xml
        assert "dpiAwareness" in xml
        assert ">PerMonitorV2<" in xml

    def test_visual_styles(self):
        """Test enabling visual styles."""
        xml = _create_manifest_xml(visual_styles=True)
        assert "Microsoft.Windows.Common-Controls" in xml
        assert 'version="6.0.0.0"' in xml
        assert "6595b64144ccf1df" in xml

    def test_uac_as_invoker(self):
        """Test UAC level set to asInvoker."""
        xml = _create_manifest_xml(uac_level="asInvoker")
        assert "trustInfo" in xml
        assert "requestedExecutionLevel" in xml
        assert 'level="asInvoker"' in xml

    def test_uac_require_administrator(self):
        """Test UAC level set to requireAdministrator."""
        xml = _create_manifest_xml(uac_level="requireAdministrator")
        assert 'level="requireAdministrator"' in xml

    def test_uac_highest_available(self):
        """Test UAC level set to highestAvailable."""
        xml = _create_manifest_xml(uac_level="highestAvailable")
        assert 'level="highestAvailable"' in xml

    def test_supported_os_win10(self):
        """Test supported OS declaration for Windows 10."""
        xml = _create_manifest_xml(supported_os=["win10"])
        assert "compatibility" in xml
        assert "supportedOS" in xml
        # Windows 10 GUID
        assert "8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a" in xml

    def test_supported_os_multiple(self):
        """Test supported OS declaration for multiple Windows versions."""
        xml = _create_manifest_xml(supported_os=["win10", "win81", "win7"])
        # Windows 10 GUID
        assert "8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a" in xml
        # Windows 8.1 GUID
        assert "1f676c76-80e1-4239-95bb-83d0f6d0da78" in xml
        # Windows 7 GUID
        assert "35138b9a-5d96-4fbd-8e2d-a2440225f93a" in xml

    def test_assembly_deps(self):
        """Test assembly dependency declarations."""
        xml = _create_manifest_xml(assembly_deps=[("MyLib.Assembly", "1.0.0.0")])
        assert "dependency" in xml
        assert "dependentAssembly" in xml
        assert 'name="MyLib.Assembly"' in xml
        assert 'version="1.0.0.0"' in xml

    def test_assembly_deps_with_arch(self):
        """Test assembly dependency with architecture."""
        xml = _create_manifest_xml(
            assembly_deps=[("MyLib.Assembly", "1.0.0.0")], arch="x64"
        )
        assert 'processorArchitecture="x64"' in xml

    def test_combined_options(self):
        """Test combining multiple manifest options."""
        xml = _create_manifest_xml(
            dpi_aware="PerMonitorV2",
            visual_styles=True,
            uac_level="asInvoker",
            supported_os=["win10"],
            assembly_deps=[("MyLib", "1.0.0.0")],
        )
        # All features should be present
        assert "dpiAware" in xml
        assert "Microsoft.Windows.Common-Controls" in xml
        assert "requestedExecutionLevel" in xml
        assert "supportedOS" in xml
        assert 'name="MyLib"' in xml


class TestCreateAssemblyManifestXml:
    """Tests for the _create_assembly_manifest_xml function."""

    def test_basic_assembly(self):
        """Test generating a basic assembly manifest."""
        xml = _create_assembly_manifest_xml(
            name="MyLib.Assembly",
            version="1.0.0.0",
            dlls=["MyLib.dll"],
            arch="x64",
        )
        assert '<?xml version="1.0" encoding="UTF-8"' in xml
        assert "urn:schemas-microsoft-com:asm.v1" in xml
        assert 'name="MyLib.Assembly"' in xml
        assert 'version="1.0.0.0"' in xml
        assert 'processorArchitecture="amd64"' in xml  # x64 maps to amd64
        assert '<file name="MyLib.dll"' in xml

    def test_multiple_dlls(self):
        """Test assembly with multiple DLLs."""
        xml = _create_assembly_manifest_xml(
            name="MyApp.Libraries",
            version="2.0.0.0",
            dlls=["Lib1.dll", "Lib2.dll", "Helper.dll"],
            arch="x86",
        )
        assert '<file name="Lib1.dll"' in xml
        assert '<file name="Lib2.dll"' in xml
        assert '<file name="Helper.dll"' in xml
        assert 'processorArchitecture="x86"' in xml

    def test_arch_mapping_x64(self):
        """Test architecture mapping for x64."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="x64"
        )
        assert 'processorArchitecture="amd64"' in xml

    def test_arch_mapping_x86_64(self):
        """Test architecture mapping for x86_64."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="x86_64"
        )
        assert 'processorArchitecture="amd64"' in xml

    def test_arch_mapping_amd64(self):
        """Test architecture mapping for amd64 (passthrough)."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="amd64"
        )
        assert 'processorArchitecture="amd64"' in xml

    def test_arch_mapping_arm64(self):
        """Test architecture mapping for arm64."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="arm64"
        )
        assert 'processorArchitecture="arm64"' in xml

    def test_arch_mapping_aarch64(self):
        """Test architecture mapping for aarch64."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="aarch64"
        )
        assert 'processorArchitecture="arm64"' in xml

    def test_arch_mapping_i386(self):
        """Test architecture mapping for i386."""
        xml = _create_assembly_manifest_xml(
            name="Test", version="1.0.0.0", dlls=["test.dll"], arch="i386"
        )
        assert 'processorArchitecture="x86"' in xml


class TestManifestXmlFormatting:
    """Tests for XML formatting in manifests."""

    def test_xml_declaration(self):
        """Test that XML declaration is present."""
        xml = _create_manifest_xml()
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')

    def test_proper_indentation(self):
        """Test that XML is properly indented."""
        xml = _create_manifest_xml(visual_styles=True)
        # Should have indented content (not all on one line)
        lines = xml.split("\n")
        # More than just the declaration and one line
        assert len(lines) > 5

    def test_assembly_root_element(self):
        """Test that root element is assembly with correct namespace."""
        xml = _create_manifest_xml()
        assert '<assembly xmlns="urn:schemas-microsoft-com:asm.v1"' in xml
