# SPDX-License-Identifier: MIT
"""Tests for config header generation."""

from __future__ import annotations

from pathlib import Path

from pcons.configure.config import Configure


class TestDefine:
    """Tests for define/undefine methods."""

    def test_define_simple(self, tmp_path: Path) -> None:
        """Test simple define."""
        config = Configure(build_dir=tmp_path)
        config.define("MY_FEATURE")

        defines = config._defines
        assert defines.get("MY_FEATURE") == 1

    def test_define_with_value(self, tmp_path: Path) -> None:
        """Test define with custom value."""
        config = Configure(build_dir=tmp_path)
        config.define("VERSION_MAJOR", 2)
        config.define("VERSION_MINOR", 5)

        defines = config._defines
        assert defines.get("VERSION_MAJOR") == 2
        assert defines.get("VERSION_MINOR") == 5

    def test_define_string_value(self, tmp_path: Path) -> None:
        """Test define with string value."""
        config = Configure(build_dir=tmp_path)
        config.define("VERSION_STRING", "1.2.3")

        defines = config._defines
        assert defines.get("VERSION_STRING") == "1.2.3"

    def test_undefine(self, tmp_path: Path) -> None:
        """Test undefine."""
        config = Configure(build_dir=tmp_path)
        config.undefine("MISSING_FEATURE")

        defines = config._defines
        assert "MISSING_FEATURE" in defines
        assert defines.get("MISSING_FEATURE") is None

    def test_defines_not_persisted_across_runs(self, tmp_path: Path) -> None:
        """A define() removed from the build script must stop appearing.

        Regression test: _defines used to be stored inside the persisted
        cache dict, so once written, "#define USE_FOO 1" would survive
        forever even after the build script stopped calling define("USE_FOO"),
        because _load_cache() merged the whole cache (including _defines)
        back in on the next run.
        """
        header_path = tmp_path / "config.h"

        # First "run": build script calls define("USE_FOO") and saves the cache.
        config1 = Configure(build_dir=tmp_path)
        config1.define("USE_FOO")
        config1.write_config_header(header_path)
        config1.save()
        assert "#define USE_FOO 1" in header_path.read_text()

        # Second "run": loads the same persisted cache, but the build script
        # no longer calls define("USE_FOO").
        config2 = Configure(build_dir=tmp_path)
        assert config2.get("USE_FOO") is None  # sanity: not in real check cache
        config2.write_config_header(header_path)

        content = header_path.read_text()
        assert "USE_FOO" not in content

    def test_check_result_cache_survives_reconfigure(self, tmp_path: Path) -> None:
        """Unlike _defines, genuine cached check results ARE meant to persist."""
        config1 = Configure(build_dir=tmp_path)
        config1.set("some_check_key", True)
        config1.save()

        config2 = Configure(build_dir=tmp_path)
        assert config2.get("some_check_key") is True


class TestCheckSizeof:
    """Tests for check_sizeof method."""

    def test_sizeof_int(self, tmp_path: Path) -> None:
        """Test sizeof(int)."""
        config = Configure(build_dir=tmp_path)
        size = config.check_sizeof("int")

        assert size is not None
        assert size == 4  # int is typically 4 bytes

        defines = config._defines
        assert defines.get("SIZEOF_INT") == 4

    def test_sizeof_pointer(self, tmp_path: Path) -> None:
        """Test sizeof(void*)."""
        config = Configure(build_dir=tmp_path)
        size = config.check_sizeof("void*")

        assert size is not None
        # Should be 8 on 64-bit, 4 on 32-bit
        assert size in (4, 8)

        defines = config._defines
        assert "SIZEOF_VOIDP" in defines

    def test_sizeof_custom_define_name(self, tmp_path: Path) -> None:
        """Test sizeof with custom define name."""
        config = Configure(build_dir=tmp_path)
        config.check_sizeof("long", define_name="MY_LONG_SIZE")

        defines = config._defines
        assert "MY_LONG_SIZE" in defines

    def test_sizeof_unknown_type(self, tmp_path: Path) -> None:
        """Test sizeof with unknown type returns default."""
        config = Configure(build_dir=tmp_path)
        size = config.check_sizeof("unknown_type_xyz", default=0)

        assert size == 0


class TestWriteConfigHeader:
    """Tests for write_config_header method."""

    def test_write_basic_header(self, tmp_path: Path) -> None:
        """Test writing a basic config header."""
        config = Configure(build_dir=tmp_path)

        # Add some defines
        config.define("MY_FEATURE")
        config.define("VERSION_MAJOR", 1)
        config.check_sizeof("int")

        header_path = tmp_path / "config.h"
        config.write_config_header(header_path)

        assert header_path.exists()
        content = header_path.read_text()

        # Check include guard
        assert "#ifndef CONFIG_H" in content
        assert "#define CONFIG_H" in content
        assert "#endif" in content

        # Check platform detection
        assert "PCONS_OS_" in content
        assert "PCONS_ARCH_" in content

        # Check defines
        assert "#define MY_FEATURE 1" in content
        assert "#define VERSION_MAJOR 1" in content
        assert "#define SIZEOF_INT" in content

    def test_write_header_with_undefines(self, tmp_path: Path) -> None:
        """Test that undefined symbols are commented out."""
        config = Configure(build_dir=tmp_path)

        config.define("HAVE_FEATURE_A")
        config.undefine("HAVE_FEATURE_B")

        header_path = tmp_path / "config.h"
        config.write_config_header(header_path)

        content = header_path.read_text()
        assert "#define HAVE_FEATURE_A 1" in content
        assert "/* #undef HAVE_FEATURE_B */" in content

    def test_write_header_custom_guard(self, tmp_path: Path) -> None:
        """Test writing header with custom include guard."""
        config = Configure(build_dir=tmp_path)

        header_path = tmp_path / "myconfig.h"
        config.write_config_header(header_path, guard="MY_CONFIG_GUARD_H")

        content = header_path.read_text()
        assert "#ifndef MY_CONFIG_GUARD_H" in content
        assert "#define MY_CONFIG_GUARD_H" in content

    def test_write_header_without_platform(self, tmp_path: Path) -> None:
        """Test writing header without platform detection."""
        config = Configure(build_dir=tmp_path)
        config.define("MY_DEFINE")

        header_path = tmp_path / "config.h"
        config.write_config_header(header_path, include_platform=False)

        content = header_path.read_text()
        assert "PCONS_OS_" not in content
        assert "PCONS_ARCH_" not in content
        assert "#define MY_DEFINE 1" in content

    def test_write_header_string_value(self, tmp_path: Path) -> None:
        """Test that string values are quoted."""
        config = Configure(build_dir=tmp_path)
        config.define("VERSION", "1.2.3")

        header_path = tmp_path / "config.h"
        config.write_config_header(header_path, include_platform=False)

        content = header_path.read_text()
        assert '#define VERSION "1.2.3"' in content

    def test_header_is_valid_c(self, tmp_path: Path) -> None:
        """Test that generated header is valid C syntax."""
        config = Configure(build_dir=tmp_path)

        config.define("HAVE_STDIO_H")
        config.define("VERSION_MAJOR", 1)
        config.define("VERSION_MINOR", 2)
        config.define("VERSION_STRING", "1.2.0")
        config.check_sizeof("int")
        config.check_sizeof("long")
        config.undefine("MISSING_FEATURE")

        header_path = tmp_path / "config.h"
        config.write_config_header(header_path)

        content = header_path.read_text()

        # Basic syntax checks
        assert content.count("#ifndef") == 1
        assert content.count("#endif") == 1
        # Every #define should have a name
        for line in content.split("\n"):
            if line.startswith("#define ") and "CONFIG_H" not in line:
                parts = line.split()
                assert len(parts) >= 2  # #define NAME [value]

    def test_write_is_skipped_when_content_unchanged(self, tmp_path: Path) -> None:
        """Rewriting identical content must not touch the file at all.

        Regression test: write_config_header() used to write unconditionally,
        bumping the header's mtime (and forcing a full rebuild of everything
        that includes it) on every run even when nothing changed.
        """
        header_path = tmp_path / "config.h"

        config1 = Configure(build_dir=tmp_path)
        config1.define("FOO", 1)
        config1.write_config_header(header_path)
        content1 = header_path.read_text()
        mtime1 = header_path.stat().st_mtime_ns

        # Second, independent "run" that produces byte-identical output.
        config2 = Configure(build_dir=tmp_path)
        config2.define("FOO", 1)
        config2.write_config_header(header_path)
        content2 = header_path.read_text()
        mtime2 = header_path.stat().st_mtime_ns

        assert content1 == content2
        assert mtime1 == mtime2  # file was not touched, so mtime is unchanged

    def test_write_happens_when_content_changes(self, tmp_path: Path) -> None:
        """Sanity check: a real content change still gets written."""
        header_path = tmp_path / "config.h"

        config1 = Configure(build_dir=tmp_path)
        config1.define("FOO", 1)
        config1.write_config_header(header_path)

        config2 = Configure(build_dir=tmp_path)
        config2.define("FOO", 2)
        config2.write_config_header(header_path)

        assert "#define FOO 2" in header_path.read_text()
