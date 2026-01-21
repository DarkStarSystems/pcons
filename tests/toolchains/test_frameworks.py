# SPDX-License-Identifier: MIT
"""Tests for macOS Framework linking support."""

from pcons.core.environment import Environment
from pcons.packages.description import PackageDescription
from pcons.toolchains.gcc import GccLinker
from pcons.toolchains.llvm import LlvmLinker


class TestGccLinkerFrameworks:
    """Test Framework support in GCC linker."""

    def test_default_vars_include_framework_support(self):
        """GCC linker should have framework-related variables."""
        link = GccLinker()
        vars = link.default_vars()
        assert vars["frameworks"] == []
        assert vars["frameworkdirs"] == []
        assert vars["fprefix"] == "-framework"
        assert vars["Fprefix"] == "-F"

    def test_progcmd_includes_framework_vars(self):
        """progcmd should expand framework variables."""
        link = GccLinker()
        vars = link.default_vars()
        progcmd = vars["progcmd"]
        # Check that framework expansion is present
        assert any("frameworkdirs" in str(item) for item in progcmd)
        assert any("frameworks" in str(item) for item in progcmd)

    def test_sharedcmd_includes_framework_vars(self):
        """sharedcmd should expand framework variables."""
        link = GccLinker()
        vars = link.default_vars()
        sharedcmd = vars["sharedcmd"]
        # Check that framework expansion is present
        assert any("frameworkdirs" in str(item) for item in sharedcmd)
        assert any("frameworks" in str(item) for item in sharedcmd)


class TestLlvmLinkerFrameworks:
    """Test Framework support in LLVM linker."""

    def test_default_vars_include_framework_support(self):
        """LLVM linker should have framework-related variables."""
        link = LlvmLinker()
        vars = link.default_vars()
        assert vars["frameworks"] == []
        assert vars["frameworkdirs"] == []
        assert vars["fprefix"] == "-framework"
        assert vars["Fprefix"] == "-F"

    def test_progcmd_includes_framework_vars(self):
        """progcmd should expand framework variables."""
        link = LlvmLinker()
        vars = link.default_vars()
        progcmd = vars["progcmd"]
        # Check that framework expansion is present
        assert any("frameworkdirs" in str(item) for item in progcmd)
        assert any("frameworks" in str(item) for item in progcmd)

    def test_sharedcmd_includes_framework_vars(self):
        """sharedcmd should expand framework variables."""
        link = LlvmLinker()
        vars = link.default_vars()
        sharedcmd = vars["sharedcmd"]
        # Check that framework expansion is present
        assert any("frameworkdirs" in str(item) for item in sharedcmd)
        assert any("frameworks" in str(item) for item in sharedcmd)


class TestEnvironmentFramework:
    """Test env.Framework() convenience method."""

    def test_framework_adds_to_link_frameworks(self):
        """Framework() should add to link.frameworks."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = []
        env.link.frameworkdirs = []

        env.Framework("Foundation")
        assert "Foundation" in env.link.frameworks

    def test_framework_multiple(self):
        """Framework() should handle multiple frameworks at once."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = []
        env.link.frameworkdirs = []

        env.Framework("Foundation", "CoreFoundation", "Metal")
        assert "Foundation" in env.link.frameworks
        assert "CoreFoundation" in env.link.frameworks
        assert "Metal" in env.link.frameworks

    def test_framework_with_dirs(self):
        """Framework() should accept custom search directories."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = []
        env.link.frameworkdirs = []

        env.Framework("MyFramework", dirs=["/custom/frameworks"])
        assert "MyFramework" in env.link.frameworks
        assert "/custom/frameworks" in env.link.frameworkdirs

    def test_framework_no_duplicates(self):
        """Framework() should not add duplicate frameworks."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = []
        env.link.frameworkdirs = []

        env.Framework("Foundation")
        env.Framework("Foundation")  # Add again
        assert env.link.frameworks.count("Foundation") == 1

    def test_framework_without_link_tool(self):
        """Framework() should do nothing if link tool doesn't exist."""
        env = Environment()
        # Don't add link tool
        env.Framework("Foundation")  # Should not raise
        assert not env.has_tool("link")

    def test_framework_creates_lists_if_missing(self):
        """Framework() should create frameworks/frameworkdirs if they don't exist."""
        env = Environment()
        link = env.add_tool("link")
        # Don't set frameworks/frameworkdirs

        env.Framework("Foundation", dirs=["/path"])
        assert hasattr(link, "frameworks")
        assert hasattr(link, "frameworkdirs")
        assert "Foundation" in link.frameworks
        assert "/path" in link.frameworkdirs


class TestEnvironmentUseWithFrameworks:
    """Test env.use() with packages that have frameworks."""

    def test_use_applies_frameworks(self):
        """use() should apply frameworks from a package."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = []
        env.link.frameworkdirs = []

        pkg = PackageDescription(
            name="metal",
            frameworks=["Metal", "Foundation"],
            framework_dirs=["/System/Library/Frameworks"],
        )

        env.use(pkg)
        assert "Metal" in env.link.frameworks
        assert "Foundation" in env.link.frameworks
        assert "/System/Library/Frameworks" in env.link.frameworkdirs

    def test_use_no_duplicate_frameworks(self):
        """use() should not add duplicate frameworks."""
        env = Environment()
        env.add_tool("link")
        env.link.frameworks = ["Foundation"]
        env.link.frameworkdirs = []

        pkg = PackageDescription(
            name="metal",
            frameworks=["Foundation", "Metal"],
        )

        env.use(pkg)
        assert env.link.frameworks.count("Foundation") == 1
        assert "Metal" in env.link.frameworks


class TestPackageDescriptionFrameworks:
    """Test PackageDescription framework support."""

    def test_framework_fields(self):
        """PackageDescription should have framework fields."""
        pkg = PackageDescription(
            name="test",
            frameworks=["Foundation"],
            framework_dirs=["/path/to/frameworks"],
        )
        assert pkg.frameworks == ["Foundation"]
        assert pkg.framework_dirs == ["/path/to/frameworks"]

    def test_get_framework_flags(self):
        """get_framework_flags() should return -framework flags."""
        pkg = PackageDescription(
            name="test",
            frameworks=["Foundation", "Metal"],
        )
        flags = pkg.get_framework_flags()
        assert flags == ["-framework", "Foundation", "-framework", "Metal"]

    def test_get_framework_dir_flags(self):
        """get_framework_dir_flags() should return -F flags."""
        pkg = PackageDescription(
            name="test",
            framework_dirs=["/path1", "/path2"],
        )
        flags = pkg.get_framework_dir_flags()
        assert flags == ["-F/path1", "-F/path2"]

    def test_get_link_flags_includes_frameworks(self):
        """get_link_flags() should include framework flags."""
        pkg = PackageDescription(
            name="test",
            libraries=["foo"],
            frameworks=["Foundation"],
            framework_dirs=["/path"],
        )
        flags = pkg.get_link_flags()
        assert "-lfoo" in flags
        assert "-F/path" in flags
        assert "-framework" in flags
        assert "Foundation" in flags

    def test_to_dict_includes_frameworks(self):
        """to_dict() should include frameworks."""
        pkg = PackageDescription(
            name="test",
            frameworks=["Foundation"],
            framework_dirs=["/path"],
        )
        d = pkg.to_dict()
        assert d["link"]["frameworks"] == ["Foundation"]
        assert d["link"]["framework_dirs"] == ["/path"]

    def test_from_dict_includes_frameworks(self):
        """from_dict() should restore frameworks."""
        data = {
            "package": {"name": "test"},
            "link": {
                "frameworks": ["Foundation", "Metal"],
                "framework_dirs": ["/path"],
            },
        }
        pkg = PackageDescription.from_dict(data)
        assert pkg.frameworks == ["Foundation", "Metal"]
        assert pkg.framework_dirs == ["/path"]


class TestPairwiseFunction:
    """Test the pairwise() substitution function."""

    def test_pairwise_basic(self):
        """pairwise() should produce interleaved pairs."""
        from pcons.core.subst import Namespace, subst

        ns = Namespace(
            {
                "prefix": "-framework",
                "items": ["Foundation", "Metal"],
            }
        )
        # Use list template to avoid tokenization on whitespace
        result = subst(["${pairwise(prefix, items)}"], ns)
        assert result == ["-framework", "Foundation", "-framework", "Metal"]

    def test_pairwise_empty_list(self):
        """pairwise() with empty list should return empty."""
        from pcons.core.subst import Namespace, subst

        ns = Namespace(
            {
                "prefix": "-framework",
                "items": [],
            }
        )
        # Use list template to avoid tokenization on whitespace
        result = subst(["${pairwise(prefix, items)}"], ns)
        assert result == []

    def test_pairwise_single_item(self):
        """pairwise() with single item should work."""
        from pcons.core.subst import Namespace, subst

        ns = Namespace(
            {
                "prefix": "-framework",
                "items": ["Foundation"],
            }
        )
        # Use list template to avoid tokenization on whitespace
        result = subst(["${pairwise(prefix, items)}"], ns)
        assert result == ["-framework", "Foundation"]


class TestFrameworkSubstitution:
    """Test that framework variables are correctly substituted in linker commands."""

    def test_framework_substitution_in_progcmd(self):
        """Framework variables should expand correctly in link commands."""
        env = Environment()
        link = env.add_tool("link")
        link.cmd = "clang"
        link.flags = []
        link.libs = []
        link.libdirs = []
        link.lprefix = "-l"
        link.Lprefix = "-L"
        link.frameworks = ["Foundation", "CoreFoundation"]
        link.frameworkdirs = ["/custom/frameworks"]
        link.fprefix = "-framework"
        link.Fprefix = "-F"
        link.progcmd = [
            "$link.cmd",
            "$link.flags",
            "-o",
            "$$TARGET",
            "$$SOURCES",
            "${prefix(link.Lprefix, link.libdirs)}",
            "${prefix(link.lprefix, link.libs)}",
            "${prefix(link.Fprefix, link.frameworkdirs)}",
            "${pairwise(link.fprefix, link.frameworks)}",
        ]

        result = env.subst_list(
            link.progcmd,
            TARGET="myapp",
            SOURCES="main.o",
        )

        assert "clang" in result
        assert "-F/custom/frameworks" in result
        assert "-framework" in result
        assert "Foundation" in result
        assert "CoreFoundation" in result

    def test_empty_frameworks_no_extra_tokens(self):
        """Empty frameworks should not add extra tokens."""
        env = Environment()
        link = env.add_tool("link")
        link.cmd = "clang"
        link.flags = []
        link.libs = []
        link.libdirs = []
        link.lprefix = "-l"
        link.Lprefix = "-L"
        link.frameworks = []
        link.frameworkdirs = []
        link.fprefix = "-framework"
        link.Fprefix = "-F"
        link.progcmd = [
            "$link.cmd",
            "-o",
            "$$TARGET",
            "$$SOURCES",
            "${prefix(link.Fprefix, link.frameworkdirs)}",
            "${pairwise(link.fprefix, link.frameworks)}",
        ]

        result = env.subst_list(
            link.progcmd,
            TARGET="myapp",
            SOURCES="main.o",
        )

        # Should just have clang -o $TARGET $SOURCES
        assert result == ["clang", "-o", "$TARGET", "$SOURCES"]
        assert "-framework" not in result
        assert "-F" not in " ".join(result)
