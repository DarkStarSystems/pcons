# SPDX-License-Identifier: MIT
"""write_if_different: unchanged generator output stays unchanged."""

import os

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.tools.stable_output import restore_unchanged, save


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
