# SPDX-License-Identifier: MIT
"""write_if_different: unchanged generator output stays unchanged."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.tools.stable_output import (
    StableOutputError,
    main,
    restore_unchanged,
    save,
)

# cmd.exe reads a leading "/" as a switch and needs /d to change drive.
CD = "cd /d" if sys.platform == "win32" else "cd"


class TestStableOutput:
    def test_identical_content_keeps_its_timestamp(self, tmp_path):
        out = tmp_path / "gen.txt"
        out.write_text("same\n")
        os.utime(out, (1_000_000, 1_000_000))
        stash = tmp_path / ".pcons-stable"

        save([out], stash)
        out.write_text("same\n")  # a naive generator rewrites unconditionally
        changed = restore_unchanged([out], stash)

        assert changed == []
        assert out.stat().st_mtime == 1_000_000

    def test_changed_content_is_reported_and_kept(self, tmp_path):
        out = tmp_path / "gen.txt"
        out.write_text("before\n")
        stash = tmp_path / ".pcons-stable"

        save([out], stash)
        out.write_text("after\n")
        changed = restore_unchanged([out], stash)

        assert changed == [out]
        assert out.read_text() == "after\n"

    def test_new_output_counts_as_changed(self, tmp_path):
        out = tmp_path / "gen.txt"
        stash = tmp_path / ".pcons-stable"

        save([out], stash)  # nothing to stash
        out.write_text("new\n")

        assert restore_unchanged([out], stash) == [out]

    def test_stash_does_not_survive_the_run(self, tmp_path):
        out = tmp_path / "gen.txt"
        out.write_text("x\n")
        stash = tmp_path / ".pcons-stable"

        save([out], stash)
        restore_unchanged([out], stash)

        assert list(stash.iterdir()) == []

    def test_same_name_in_different_directories(self, tmp_path):
        first, second = tmp_path / "a", tmp_path / "b"
        first.mkdir()
        second.mkdir()
        (first / "gen.txt").write_text("one\n")
        (second / "gen.txt").write_text("two\n")
        stash = tmp_path / ".pcons-stable"
        outputs = [first / "gen.txt", second / "gen.txt"]

        save(outputs, stash)
        (first / "gen.txt").write_text("one\n")
        (second / "gen.txt").write_text("changed\n")

        assert restore_unchanged(outputs, stash) == [second / "gen.txt"]


class TestCommandOption:
    def test_wraps_the_command_and_implies_restat(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=project.build_dir / "gen.txt",
            source=None,
            command="generate $TARGET",
            write_if_different=True,
        )

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        assert "stable_output --pre $out && generate" in content
        assert "&& " in content and "stable_output --post $out" in content
        assert "restat = 1" in content


class TestChangedDirectoryIsCaught:
    """The failure this whole check exists for.

    The wrapper is `--pre $out && <command> && --post $out`, and both halves
    name their files relative to where the build system started them. A `cd`
    inside <command> leaves `--post` somewhere else: it found none of the
    stashed outputs, restored nothing and exited 0, so restat silently stopped
    suppressing anything -- a 15 MB relink per build with nothing to see. It
    now fails instead."""

    def test_post_in_another_directory_fails(self, tmp_path, monkeypatch):
        build = tmp_path / "build"
        build.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (build / "gen.txt").write_text("same\n")

        monkeypatch.chdir(build)
        assert main(["--pre", "gen.txt"]) == 0

        monkeypatch.chdir(elsewhere)  # what a `cd` in the command does
        assert main(["--post", "gen.txt"]) == 1

    def test_the_message_names_the_cause(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(StableOutputError, match="same directory"):
            restore_unchanged([Path("gen.txt")], Path(".pcons-stable"))

    def test_a_stash_from_another_build_is_refused(self, tmp_path, monkeypatch):
        first, second = tmp_path / "one", tmp_path / "two"
        for directory in (first, second):
            directory.mkdir()
        monkeypatch.chdir(first)
        save([Path("gen.txt")], Path(".pcons-stable"))

        # Same relative output name, so the record has the same name too --
        # only the recorded directory tells them apart.
        shutil.copytree(first / ".pcons-stable", second / ".pcons-stable")
        monkeypatch.chdir(second)
        with pytest.raises(StableOutputError, match="another build"):
            restore_unchanged([Path("gen.txt")], Path(".pcons-stable"))

    def test_nothing_to_restore_is_not_a_failure(self, tmp_path, monkeypatch):
        """First build: --pre had no outputs to stash. That is normal."""
        monkeypatch.chdir(tmp_path)
        assert main(["--pre", "gen.txt"]) == 0
        Path("gen.txt").write_text("new\n")

        assert main(["--post", "gen.txt"]) == 0

    def test_command_cwd_keeps_the_wrapper_whole(self, tmp_path, gcc_toolchain):
        """The end-to-end claim: cwd= moves the command, not the wrapper."""
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.Command(
            target=project.build_dir / "gen.txt",
            source=None,
            command="generate $TARGET",
            cwd=tmp_path,
            write_if_different=True,
        )

        NinjaGenerator().generate(project)
        BaseGenerator._generate_pending(project)

        content = (tmp_path / "build" / "build.ninja").read_text()
        command = next(
            line for line in content.splitlines() if "stable_output --pre" in line
        )
        pre, rest = command.split(f" && {CD} .. && ", 1)
        moved, post = rest.split(f" && {CD} build && ", 1)
        assert "stable_output --pre $out" in pre
        assert "stable_output --post $out" in post
        assert "stable_output" not in moved

    def test_a_vanished_stash_is_not_reported_as_changed(self, tmp_path, monkeypatch):
        """Silently treating a lost stash as "the output changed" would put
        the rebuild back without saying so."""
        monkeypatch.chdir(tmp_path)
        stash = Path(".pcons-stable")
        Path("gen.txt").write_text("same\n")
        save([Path("gen.txt")], stash)

        for stashed in stash.glob("gen.txt.*"):
            stashed.unlink()

        with pytest.raises(StableOutputError, match="no longer in"):
            restore_unchanged([Path("gen.txt")], stash)
