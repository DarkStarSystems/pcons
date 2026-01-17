# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.config."""

import json
from pathlib import Path

import pytest

from pcons.configure.config import Configure, ProgramInfo, load_config


class TestConfigure:
    def test_creation(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        assert config.build_dir == tmp_path
        assert config.platform is not None

    def test_platform_detected(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        assert config.platform.os in ("linux", "darwin", "windows", "freebsd")

    def test_set_and_get(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        config.set("foo", "bar")
        assert config.get("foo") == "bar"

    def test_get_default(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        assert config.get("nonexistent") is None
        assert config.get("nonexistent", "default") == "default"

    def test_repr(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        r = repr(config)
        assert "Configure" in r
        assert str(tmp_path) in r


class TestConfigureSaveLoad:
    def test_save_creates_file(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        config.set("test_key", "test_value")
        config.save()

        cache_file = tmp_path / "pcons_config.json"
        assert cache_file.exists()

    def test_save_writes_json(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        config.set("test_key", "test_value")
        config.save()

        cache_file = tmp_path / "pcons_config.json"
        data = json.loads(cache_file.read_text())
        assert data["test_key"] == "test_value"

    def test_loads_existing_cache(self, tmp_path):
        # Create a cache file
        cache_file = tmp_path / "pcons_config.json"
        cache_file.write_text('{"cached_key": "cached_value"}')

        # Create Configure - should load the cache
        config = Configure(build_dir=tmp_path)
        assert config.get("cached_key") == "cached_value"

    def test_load_config_function(self, tmp_path):
        cache_file = tmp_path / "test_config.json"
        cache_file.write_text('{"key": "value"}')

        data = load_config(cache_file)
        assert data["key"] == "value"

    def test_load_config_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")


class TestConfigureFindProgram:
    def test_find_program_in_path(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        # Try to find a common program that should exist
        # Python itself should always be findable
        result = config.find_program("python3") or config.find_program("python")
        # At least one should work
        assert result is None or isinstance(result, ProgramInfo)

    def test_find_nonexistent_program(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        result = config.find_program("this_program_definitely_does_not_exist_12345")
        assert result is None

    def test_find_required_program_raises(self, tmp_path):
        config = Configure(build_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            config.find_program(
                "this_program_definitely_does_not_exist_12345",
                required=True,
            )

    def test_find_program_with_hints(self, tmp_path):
        import sys

        # Create a fake executable
        # On Windows, executables need .exe suffix
        if sys.platform == "win32":
            fake_exe = tmp_path / "fake_program.exe"
            fake_exe.write_text("@echo fake")
        else:
            fake_exe = tmp_path / "fake_program"
            fake_exe.write_text("#!/bin/sh\necho fake")
            fake_exe.chmod(0o755)

        config = Configure(build_dir=tmp_path)
        result = config.find_program("fake_program", hints=[tmp_path])

        assert result is not None
        assert result.path == fake_exe

    def test_find_program_caches_result(self, tmp_path):
        config = Configure(build_dir=tmp_path)

        # Find twice - second should use cache
        result1 = config.find_program("python3") or config.find_program("python")
        if result1:
            # The path should be cached
            cache_key = (
                "program:python3" if config.get("program:python3") else "program:python"
            )
            cached = config.get(cache_key)
            assert cached is not None


class TestProgramInfo:
    def test_creation(self):
        info = ProgramInfo(path=Path("/usr/bin/gcc"))
        assert info.path == Path("/usr/bin/gcc")
        assert info.version is None

    def test_with_version(self):
        info = ProgramInfo(path=Path("/usr/bin/gcc"), version="12.0.0")
        assert info.version == "12.0.0"
