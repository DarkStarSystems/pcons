# SPDX-License-Identifier: MIT
"""Tests for `pcons --watch` (pcons.watch)."""

from __future__ import annotations

import argparse
import importlib.util
import threading
import time
from pathlib import Path

import pytest

from pcons import watch
from pcons.core.errors import PconsError


class TestIgnoreRules:
    """What a watch must not react to."""

    @pytest.mark.parametrize(
        "relative",
        [
            "build/hello.o",  # the build's own output: would loop forever
            "build/nested/deep/hello.o",
            "compile_commands.json",  # symlink pcons maintains at the root
            ".compile_commands.json.123.abcdef",  # its atomic-swap temp name
            ".git/index",
            "src/__pycache__/mod.cpython-311.pyc",
            "src/mod.pyc",
            ".venv/lib/thing.py",
            "node_modules/pkg/index.js",
            "src/.#main.c",  # emacs lock
            "src/#main.c#",  # emacs autosave
            "src/main.c~",  # editor backup
            "src/.main.c.swp",  # vim swap
            "src/.DS_Store",
        ],
    )
    def test_ignored(self, tmp_path: Path, relative: str) -> None:
        ignored = watch.make_ignore([tmp_path / "build"])
        assert ignored(tmp_path / relative)

    @pytest.mark.parametrize(
        "relative",
        [
            "src/main.c",
            "include/math_ops.h",
            "pcons-build.py",
            "src/nested/util.cpp",
            "data/shader.glsl",
            "rebuild.py",  # not a temp file despite living beside build outputs
        ],
    )
    def test_watched(self, tmp_path: Path, relative: str) -> None:
        ignored = watch.make_ignore([tmp_path / "build"])
        assert not ignored(tmp_path / relative)

    def test_build_dir_outside_the_tree(self, tmp_path: Path) -> None:
        """An out-of-tree build directory is excluded just the same."""
        ignored = watch.make_ignore([tmp_path / "elsewhere" / "build"])
        assert ignored(tmp_path / "elsewhere" / "build" / "hello.o")
        assert not ignored(tmp_path / "elsewhere" / "src" / "hello.c")


class TestExcludedOutputs:
    """Build outputs must not retrigger the build that wrote them."""

    def test_declared_output_in_the_source_tree(self, tmp_path: Path) -> None:
        generated = tmp_path / "src" / "generated.txt"
        ignored = watch.make_ignore(excluded_paths={generated})
        assert ignored(generated)
        assert not ignored(tmp_path / "src" / "main.c")

    def test_depfile_beside_its_output(self, tmp_path: Path) -> None:
        """Depfiles are written next to an output but are never outputs."""
        obj = tmp_path / "obj" / "main.c.o"
        ignored = watch.make_ignore(excluded_paths={obj})
        assert ignored(obj.with_name("main.c.o.d"))

    def test_d_source_is_not_mistaken_for_a_depfile(self, tmp_path: Path) -> None:
        """`.d` is a real source extension; only an actual output's sibling goes."""
        ignored = watch.make_ignore(excluded_paths={tmp_path / "obj" / "main.c.o"})
        assert not ignored(tmp_path / "src" / "app.d")

    def test_outputs_are_consulted_live(self, tmp_path: Path) -> None:
        """A regenerated manifest changes what is ignored, without restarting."""
        outputs: set[Path] = set()
        ignored = watch.make_ignore(excluded_paths=outputs)
        generated = tmp_path / "src" / "generated.txt"
        assert not ignored(generated)

        outputs.add(generated)
        assert ignored(generated)


class TestLoopDetector:
    """Telling a self-feeding watch apart from someone typing fast."""

    def _spin(self, detector: watch.LoopDetector, path: Path, rounds: int):
        result = frozenset()
        for _ in range(rounds):
            result = detector.record({path}, gap=0.05)
        return result

    def test_detects_a_repeating_immediate_trigger(self, tmp_path: Path) -> None:
        detector = watch.LoopDetector()
        generated = tmp_path / "src" / "generated.txt"
        assert self._spin(detector, generated, watch.LOOP_ROUNDS) == {generated}

    def test_quiet_below_the_round_count(self, tmp_path: Path) -> None:
        detector = watch.LoopDetector()
        culprits = self._spin(detector, tmp_path / "a.txt", watch.LOOP_ROUNDS - 1)
        assert not culprits

    def test_a_human_pause_resets_it(self, tmp_path: Path) -> None:
        """Saving one file over and over is not a loop, however often."""
        detector = watch.LoopDetector()
        source = tmp_path / "src" / "main.c"
        for _ in range(watch.LOOP_ROUNDS * 3):
            assert not detector.record({source}, gap=watch.IMMEDIATE_SECONDS + 0.5)

    def test_immediate_but_unrelated_triggers_are_not_a_loop(
        self, tmp_path: Path
    ) -> None:
        detector = watch.LoopDetector()
        culprits = frozenset()
        for i in range(watch.LOOP_ROUNDS):
            culprits = detector.record({tmp_path / f"file{i}.c"}, gap=0.05)
        assert not culprits

    def test_reports_only_the_common_path(self, tmp_path: Path) -> None:
        """The file present every round is the culprit; incidental ones are not."""
        detector = watch.LoopDetector()
        generated = tmp_path / "generated.txt"
        culprits = frozenset()
        for i in range(watch.LOOP_ROUNDS):
            culprits = detector.record({generated, tmp_path / f"other{i}"}, gap=0.05)
        assert culprits == {generated}


class TestWatchLoop:
    """The rebuild loop, driven by an injected change stream."""

    def test_builds_once_per_batch(self, tmp_path: Path) -> None:
        builds = []
        changes = [
            {tmp_path / "src/main.c"},
            {tmp_path / "src/util.c", tmp_path / "src/main.c"},
        ]

        result = watch.watch_and_build(
            lambda: builds.append("built") or 0,
            [tmp_path],
            excluded_dirs=[tmp_path / "build"],
            changes=changes,
        )

        assert result == 0
        assert len(builds) == 2

    def test_batch_of_only_ignored_paths_does_not_build(self, tmp_path: Path) -> None:
        builds = []
        changes = [
            {tmp_path / "build" / "main.o", tmp_path / "compile_commands.json"},
            {tmp_path / "src" / "main.c"},
        ]

        watch.watch_and_build(
            lambda: builds.append("built") or 0,
            [tmp_path],
            excluded_dirs=[tmp_path / "build"],
            changes=changes,
        )

        assert len(builds) == 1

    def test_failed_build_keeps_watching(self, tmp_path: Path) -> None:
        """The next edit is usually the fix, so a failure must not end the watch."""
        codes = iter([1, 0])
        builds = []

        def build() -> int:
            code = next(codes)
            builds.append(code)
            return code

        result = watch.watch_and_build(
            build,
            [tmp_path],
            changes=[{tmp_path / "a.c"}, {tmp_path / "b.c"}],
        )

        assert result == 0
        assert builds == [1, 0]

    def test_build_raising_keeps_watching(self, tmp_path: Path) -> None:
        builds = []

        def build() -> int:
            builds.append("built")
            if len(builds) == 1:
                raise PconsError("no C compiler found")
            return 0

        result = watch.watch_and_build(
            build, [tmp_path], changes=[{tmp_path / "a.c"}, {tmp_path / "b.c"}]
        )

        assert result == 0
        assert len(builds) == 2

    def test_interrupt_during_build_stops_cleanly(self, tmp_path: Path) -> None:
        builds = []

        def build() -> int:
            builds.append("built")
            raise KeyboardInterrupt

        result = watch.watch_and_build(
            build, [tmp_path], changes=[{tmp_path / "a.c"}, {tmp_path / "b.c"}]
        )

        assert result == 0
        assert len(builds) == 1  # the second batch is never built

    def test_a_rebuild_loop_stops_the_watch(self, tmp_path: Path) -> None:
        """Injected batches arrive instantly, which is what a real loop does."""
        builds = []
        generated = tmp_path / "src" / "generated.txt"

        result = watch.watch_and_build(
            lambda: builds.append("built") or 0,
            [tmp_path],
            changes=[{generated}] * (watch.LOOP_ROUNDS + 5),
        )

        assert result == 1  # stopped, and said why
        assert len(builds) == watch.LOOP_ROUNDS


class TestNinjaQueries:
    """Parsing what ninja reports about its own graph."""

    def test_targets_are_absolute_and_normalized(self, tmp_path: Path) -> None:
        """An output above the build dir must match the path the watch sees."""
        from pcons.cli import _parse_ninja_targets

        build_dir = tmp_path / "build"
        outputs = _parse_ninja_targets(
            "obj/main.c.o: cc\n../src/generated.txt: command_x\n", build_dir
        )

        assert outputs == {
            build_dir / "obj" / "main.c.o",
            tmp_path / "src" / "generated.txt",
        }

    def test_phony_aliases_are_skipped(self, tmp_path: Path) -> None:
        """`all: phony` names an alias, which could collide with a real dir."""
        from pcons.cli import _parse_ninja_targets

        assert _parse_ninja_targets("all: phony\nhello: phony\n", tmp_path) == set()

    def test_a_windows_drive_letter_is_not_a_separator(self, tmp_path: Path) -> None:
        from pcons.cli import _parse_ninja_targets

        outputs = _parse_ninja_targets("C:/proj/build/main.obj: cc\n", tmp_path)

        assert len(outputs) == 1
        assert next(iter(outputs)).name == "main.obj"

    def test_explain_reasons_are_extracted(self) -> None:
        from pcons.cli import _explain_reasons

        output = (
            "ninja: Entering directory `build'\n"
            "ninja explain: output declared.txt doesn't exist\n"
            "[1/1] COMMAND declared.txt\n"
        )

        assert _explain_reasons(output) == ["output declared.txt doesn't exist"]


class TestAvailability:
    """--watch needs the optional watchfiles package."""

    def test_available_when_installed(self) -> None:
        pytest.importorskip("watchfiles")
        watch.ensure_available()  # does not raise

    def test_missing_package_explains_how_to_install(self, monkeypatch) -> None:
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
        with pytest.raises(PconsError, match="pcons\\[watch\\]"):
            watch.ensure_available()

    def test_cli_reports_missing_package_without_building(self, monkeypatch) -> None:
        """The check runs before the first build, not after a long compile."""
        from pcons import cli

        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
        monkeypatch.setattr(
            cli, "_build_targets", lambda _args: pytest.fail("should not build")
        )
        args = argparse.Namespace(
            watch=True, verbose=False, debug=None, build_dir="build"
        )

        assert cli.cmd_build(args) == 1


def test_native_watcher_reports_a_change(tmp_path: Path) -> None:
    """The watchfiles wiring really does deliver changes."""
    pytest.importorskip("watchfiles")

    stop = threading.Event()
    deadline = threading.Timer(10.0, stop.set)  # bound the test if nothing arrives
    deadline.daemon = True
    deadline.start()

    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }\n")
    changes = watch._watchfiles_changes([tmp_path], watch.make_ignore(), stop)

    # Keep editing until a change comes back, rather than writing once and
    # hoping the watcher was already running: startup is not observable.
    done = threading.Event()

    def keep_editing() -> None:
        while not done.wait(0.2):
            source.write_text(f"int main(void) {{ return {time.monotonic()}; }}\n")

    writer = threading.Thread(target=keep_editing, daemon=True)
    writer.start()
    try:
        batch = next(changes, None)
    finally:
        done.set()
        stop.set()
        deadline.cancel()
        writer.join(timeout=5)

    assert batch is not None, "no change reported within 10s"
    assert source in batch
