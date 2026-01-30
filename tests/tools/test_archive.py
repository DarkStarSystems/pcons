# SPDX-License-Identifier: MIT
"""Tests for archive builders (Tarfile and Zipfile)."""

import tarfile
import zipfile
from pathlib import Path

from pcons.core.project import Project
from pcons.core.target import Target, TargetType
from pcons.generators.ninja import NinjaGenerator


class TestTarfileBuilder:
    """Tests for project.Tarfile() builder."""

    def test_tarfile_returns_target(self, tmp_path):
        """Tarfile() returns a Target object."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        tar = project.Tarfile(
            env,
            output="dist/archive.tar.gz",
            sources=["file1.txt", "file2.txt"],
        )

        assert isinstance(tar, Target)
        assert tar.target_type == TargetType.ARCHIVE

    def test_tarfile_name_derived_from_output(self, tmp_path):
        """Tarfile name is derived from output path."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Test various compression extensions
        tar_gz = project.Tarfile(env, output="dist/docs.tar.gz", sources=[])
        assert tar_gz.name == "dist/docs"

        tar_bz2 = project.Tarfile(env, output="dist/backup.tar.bz2", sources=[])
        assert tar_bz2.name == "dist/backup"

        tar_xz = project.Tarfile(env, output="dist/data.tar.xz", sources=[])
        assert tar_xz.name == "dist/data"

        tar_plain = project.Tarfile(env, output="dist/plain.tar", sources=[])
        assert tar_plain.name == "dist/plain"

        tgz = project.Tarfile(env, output="dist/short.tgz", sources=[])
        assert tgz.name == "dist/short"

    def test_tarfile_explicit_name(self, tmp_path):
        """Tarfile can have explicit name."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        tar = project.Tarfile(
            env,
            output="out.tar.gz",
            sources=[],
            name="my_archive",
        )

        assert tar.name == "my_archive"

    def test_tarfile_compression_inferred_from_extension(self, tmp_path):
        """Compression type is inferred from output extension."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create tarfiles with different extensions
        tar_gz = project.Tarfile(env, output="a.tar.gz", sources=[], name="a")
        tar_bz2 = project.Tarfile(env, output="b.tar.bz2", sources=[], name="b")
        tar_xz = project.Tarfile(env, output="c.tar.xz", sources=[], name="c")
        tar_plain = project.Tarfile(env, output="d.tar", sources=[], name="d")
        tgz = project.Tarfile(env, output="e.tgz", sources=[], name="e")

        assert tar_gz._builder_data["compression"] == "gzip"
        assert tar_bz2._builder_data["compression"] == "bz2"
        assert tar_xz._builder_data["compression"] == "xz"
        assert tar_plain._builder_data["compression"] is None
        assert tgz._builder_data["compression"] == "gzip"

    def test_tarfile_explicit_compression(self, tmp_path):
        """Explicit compression overrides inferred."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Override gzip with bz2
        tar = project.Tarfile(
            env,
            output="archive.tar.gz",
            sources=[],
            compression="bz2",
            name="override",
        )

        assert tar._builder_data["compression"] == "bz2"

    def test_tarfile_base_dir(self, tmp_path):
        """Tarfile respects base_dir parameter."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        tar = project.Tarfile(
            env,
            output="archive.tar.gz",
            sources=[],
            base_dir="src",
            name="with_base",
        )

        assert tar._builder_data["base_dir"] == "src"

    def test_tarfile_default_base_dir(self, tmp_path):
        """Tarfile uses '.' as default base_dir."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        tar = project.Tarfile(
            env, output="archive.tar.gz", sources=[], name="default_base"
        )

        assert tar._builder_data["base_dir"] == "."


class TestZipfileBuilder:
    """Tests for project.Zipfile() builder."""

    def test_zipfile_returns_target(self, tmp_path):
        """Zipfile() returns a Target object."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        zf = project.Zipfile(
            env,
            output="dist/archive.zip",
            sources=["file1.txt", "file2.txt"],
        )

        assert isinstance(zf, Target)
        assert zf.target_type == TargetType.ARCHIVE

    def test_zipfile_name_derived_from_output(self, tmp_path):
        """Zipfile name is derived from output path."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        zf = project.Zipfile(env, output="dist/release.zip", sources=[])
        assert zf.name == "dist/release"

    def test_zipfile_explicit_name(self, tmp_path):
        """Zipfile can have explicit name."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        zf = project.Zipfile(
            env,
            output="out.zip",
            sources=[],
            name="my_zip",
        )

        assert zf.name == "my_zip"

    def test_zipfile_base_dir(self, tmp_path):
        """Zipfile respects base_dir parameter."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        zf = project.Zipfile(
            env,
            output="archive.zip",
            sources=[],
            base_dir="src",
            name="with_base",
        )

        assert zf._builder_data["base_dir"] == "src"


class TestArchiveTargetProperties:
    """Tests for ArchiveTarget properties (compression, basedir)."""

    def test_tarfile_compression_property(self, tmp_path):
        """ArchiveTarget.compression property allows override."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create tarfile with default compression inferred from extension
        tar = project.Tarfile(env, output="archive.tar.gz", sources=[], name="test")
        assert tar.compression == "gzip"  # Inferred from extension

        # Override compression after creation
        tar.compression = "xz"
        assert tar.compression == "xz"

    def test_tarfile_basedir_property(self, tmp_path):
        """ArchiveTarget.basedir property allows override."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create tarfile with default basedir
        tar = project.Tarfile(env, output="archive.tar.gz", sources=[], name="test")
        assert tar.basedir == "."

        # Override basedir after creation
        tar.basedir = "custom/path"
        assert tar.basedir == "custom/path"

    def test_zipfile_basedir_property(self, tmp_path):
        """Zipfile also supports basedir override."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        zf = project.Zipfile(env, output="archive.zip", sources=[], name="test")
        assert zf.basedir == "."

        zf.basedir = "src"
        assert zf.basedir == "src"

    def test_property_override_used_in_context(self, tmp_path):
        """Property overrides are picked up by ArchiveContext."""
        from pcons.tools.archive_context import ArchiveContext

        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        tar = project.Tarfile(
            env, output="archive.tar.gz", sources=[], name="test", base_dir="initial"
        )

        # Override after creation
        tar.compression = "bz2"
        tar.basedir = "overridden"

        # Create context and verify it uses the overridden values
        context = ArchiveContext.from_target(tar, env)
        assert context.compression == "bz2"
        assert context.basedir == "overridden"


class TestArchiveNinjaGeneration:
    """Tests for Ninja generation of archive targets."""

    def test_generates_tarfile_rule(self, tmp_path):
        """Ninja generator creates rule for tarfile."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create a source file
        src_file = tmp_path / "data.txt"
        src_file.write_text("test content")

        project.Tarfile(
            env,
            output="dist/archive.tar.gz",
            sources=[str(src_file)],
            name="tar_rule_test",
        )

        project.resolve()
        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()

        # Should have archive tool rule for tar with compression baked in
        assert "rule archive_tarcmd" in content
        # Rule should use archive_helper script
        assert "archive_helper.py" in content
        assert "--type tar" in content
        # Compression flag should be baked into the rule command (not per-build variable)
        # The rule command should contain "--compression gzip"
        assert "--compression gzip" in content
        # There should NOT be a per-build variable for compression_flag
        assert "compression_flag = --compression" not in content

    def test_generates_zipfile_rule(self, tmp_path):
        """Ninja generator creates rule for zipfile."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create a source file
        src_file = tmp_path / "data.txt"
        src_file.write_text("test content")

        project.Zipfile(
            env,
            output="dist/archive.zip",
            sources=[str(src_file)],
            name="zip_rule_test",
        )

        project.resolve()
        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()

        # Should have archive tool rule for zip
        assert "rule archive_zipcmd" in content
        # Rule should use archive_helper script
        assert "archive_helper.py" in content
        assert "--type zip" in content

    def test_generates_build_statement(self, tmp_path):
        """Ninja generator creates build statements for archives."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create source files
        file1 = tmp_path / "file1.txt"
        file1.write_text("content 1")
        file2 = tmp_path / "file2.txt"
        file2.write_text("content 2")

        project.Tarfile(
            env,
            output="output.tar.gz",
            sources=[str(file1), str(file2)],
            name="build_test",
        )

        project.resolve()
        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()

        # Should have build statement for the archive
        assert "build output.tar.gz:" in content
        # Sources should be listed
        assert "file1.txt" in content
        assert "file2.txt" in content

    def test_basedir_with_spaces_is_quoted(self, tmp_path):
        """Basedir with spaces must be properly quoted for shell execution."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create source file in directory with spaces
        src_dir = tmp_path / "source files"
        src_dir.mkdir()
        src_file = src_dir / "data.txt"
        src_file.write_text("test content")

        # Create tarfile with basedir containing spaces
        project.Tarfile(
            env,
            output="archive.tar.gz",
            sources=[str(src_file)],
            base_dir=str(src_dir),  # Path with spaces
            name="space_test",
        )

        project.resolve()
        gen = NinjaGenerator()
        gen.generate(project)

        content = (tmp_path / "build.ninja").read_text()

        # The basedir path with spaces MUST be quoted for the shell
        # Without quoting, "source files" becomes two separate arguments
        # Look for the command - it should have proper quoting
        # Either single quotes 'source files' or escaped "source\ files"
        assert "source files" in content  # The path should appear
        # The path must be quoted - check it's not just bare "source files"
        # which would be split by the shell into two args
        import re

        # Find the command line in the rule (rule name includes hash)
        rule_match = re.search(r"rule archive_tarcmd_\w+\s+command = (.+)", content)
        assert rule_match, f"Should have archive rule in:\n{content}"
        command = rule_match.group(1)

        # The basedir path with space must be quoted for shell execution
        # It should appear as 'source files' or "source files" or source\ files
        # NOT as bare: --base-dir /path/to/source files (which would be wrong)
        # Check that the pattern "--base-dir <path>source files" is properly quoted
        basedir_match = re.search(r"--base-dir\s+(\S*source files\S*)", command)
        assert basedir_match, (
            f"Should have --base-dir with 'source files' in: {command}"
        )
        basedir_arg = basedir_match.group(1)

        # The basedir argument must be quoted - not bare
        # Bare would be: /path/to/source (then "files" as separate arg)
        # Quoted would be: '/path/to/source files' or "/path/to/source files"
        assert (
            basedir_arg.startswith("'")
            or basedir_arg.startswith('"')
            or "\\ " in basedir_arg  # backslash-escaped space
        ), f"Basedir with spaces not properly quoted: {basedir_arg}"


class TestArchiveWithInstall:
    """Tests for archives used with Install."""

    def test_archive_can_be_installed(self, tmp_path):
        """Archives can be passed to Install() since they are Targets."""
        project = Project("test", root_dir=tmp_path, build_dir=".")
        env = project.Environment()

        # Create a source file
        src_file = tmp_path / "data.txt"
        src_file.write_text("test content")

        # Create archive
        tar = project.Tarfile(
            env,
            output="dist/archive.tar.gz",
            sources=[str(src_file)],
            name="installable_archive",
        )

        # Install the archive
        install = project.Install("packages/", [tar])

        # Resolve should work
        project.resolve()

        # Archive should have output nodes
        assert len(tar.output_nodes) == 1
        assert tar.output_nodes[0].path == Path("dist/archive.tar.gz")

        # Install should reference the archive's output
        assert install._pending_sources is None  # Resolved
        assert len(install.output_nodes) > 0


class TestArchiveHelper:
    """Tests for the archive_helper module."""

    def test_create_tarfile_gzip(self, tmp_path):
        """archive_helper creates gzipped tarball."""
        from pcons.util.archive_helper import create_tarfile

        # Create test files
        file1 = tmp_path / "file1.txt"
        file1.write_text("content 1")
        file2 = tmp_path / "dir" / "file2.txt"
        file2.parent.mkdir()
        file2.write_text("content 2")

        output = tmp_path / "out.tar.gz"
        create_tarfile(output, [file1, file2], "gzip", tmp_path)

        assert output.exists()

        # Verify contents
        with tarfile.open(output, "r:gz") as tf:
            names = tf.getnames()
            assert "file1.txt" in names
            assert "dir/file2.txt" in names

    def test_create_tarfile_bz2(self, tmp_path):
        """archive_helper creates bz2 tarball."""
        from pcons.util.archive_helper import create_tarfile

        file1 = tmp_path / "file1.txt"
        file1.write_text("content")

        output = tmp_path / "out.tar.bz2"
        create_tarfile(output, [file1], "bz2", tmp_path)

        assert output.exists()

        with tarfile.open(output, "r:bz2") as tf:
            assert "file1.txt" in tf.getnames()

    def test_create_tarfile_xz(self, tmp_path):
        """archive_helper creates xz tarball."""
        from pcons.util.archive_helper import create_tarfile

        file1 = tmp_path / "file1.txt"
        file1.write_text("content")

        output = tmp_path / "out.tar.xz"
        create_tarfile(output, [file1], "xz", tmp_path)

        assert output.exists()

        with tarfile.open(output, "r:xz") as tf:
            assert "file1.txt" in tf.getnames()

    def test_create_tarfile_uncompressed(self, tmp_path):
        """archive_helper creates uncompressed tarball."""
        from pcons.util.archive_helper import create_tarfile

        file1 = tmp_path / "file1.txt"
        file1.write_text("content")

        output = tmp_path / "out.tar"
        create_tarfile(output, [file1], None, tmp_path)

        assert output.exists()

        with tarfile.open(output, "r:") as tf:
            assert "file1.txt" in tf.getnames()

    def test_create_zipfile(self, tmp_path):
        """archive_helper creates zip archive."""
        from pcons.util.archive_helper import create_zipfile

        file1 = tmp_path / "file1.txt"
        file1.write_text("content 1")
        file2 = tmp_path / "dir" / "file2.txt"
        file2.parent.mkdir()
        file2.write_text("content 2")

        output = tmp_path / "out.zip"
        create_zipfile(output, [file1, file2], tmp_path)

        assert output.exists()

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            assert "file1.txt" in names
            assert "dir/file2.txt" in names

    def test_expand_directories(self, tmp_path):
        """archive_helper expands directories to file lists."""
        from pcons.util.archive_helper import expand_directories

        # Create directory structure
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        (dir1 / "a.txt").write_text("a")
        (dir1 / "b.txt").write_text("b")

        subdir = dir1 / "subdir"
        subdir.mkdir()
        (subdir / "c.txt").write_text("c")

        # Also a standalone file
        standalone = tmp_path / "standalone.txt"
        standalone.write_text("standalone")

        result = expand_directories([dir1, standalone])

        # Should have expanded to individual files
        assert len(result) == 4
        names = [f.name for f in result]
        assert "a.txt" in names
        assert "b.txt" in names
        assert "c.txt" in names
        assert "standalone.txt" in names

    def test_base_dir_path_stripping(self, tmp_path):
        """Files outside base_dir use just filename in archive."""
        from pcons.util.archive_helper import create_tarfile

        # Create file in a different location
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        outside_file = other_dir / "outside.txt"
        outside_file.write_text("outside")

        # base_dir doesn't contain the file
        base_dir = tmp_path / "base"
        base_dir.mkdir()

        output = tmp_path / "out.tar.gz"
        create_tarfile(output, [outside_file], "gzip", base_dir)

        with tarfile.open(output, "r:gz") as tf:
            names = tf.getnames()
            # File outside base_dir should use just filename
            assert "outside.txt" in names
