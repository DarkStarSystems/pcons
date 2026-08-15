# SPDX-License-Identifier: MIT
"""Tests for `pcons --watch` (pcons.watch)."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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

    def test_ctrl_c_during_a_build_stops_the_watch(self, tmp_path: Path) -> None:
        """The handler asks for a stop; the loop checks after the build."""
        import signal

        builds = []

        def build() -> int:
            builds.append("built")
            signal.raise_signal(signal.SIGINT)  # as if the user pressed Ctrl-C
            return 0

        result = watch.watch_and_build(
            build, [tmp_path], changes=[{tmp_path / "a.c"}, {tmp_path / "b.c"}]
        )

        assert result == 0
        assert len(builds) == 1  # the second batch is never built

    def test_an_unexpected_exception_keeps_watching(self, tmp_path: Path) -> None:
        """A bug in the build path must not silently end the session."""
        builds = []

        def build() -> int:
            builds.append("built")
            if len(builds) == 1:
                raise RuntimeError("something unforeseen")
            return 0

        result = watch.watch_and_build(
            build, [tmp_path], changes=[{tmp_path / "a.c"}, {tmp_path / "b.c"}]
        )

        assert result == 0
        assert len(builds) == 2

    def test_watching_off_the_main_thread(self, tmp_path: Path) -> None:
        """Signal handlers are main-thread only, so the watch does without."""
        outcome: list[int] = []

        def run() -> None:
            outcome.append(
                watch.watch_and_build(
                    lambda: 0, [tmp_path], changes=[{tmp_path / "a.c"}]
                )
            )

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)

        assert outcome == [0]


class TestCliWiring:
    """What `pcons build --watch` hands to the watch."""

    def _run(self, monkeypatch, tmp_path: Path, build_dir: Path) -> dict:
        """Drive the watch with everything below it stubbed out."""
        from pcons import cli

        captured: dict = {}
        script = tmp_path / "pcons-build.py"
        script.write_text("")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "ninja_outputs", lambda *a, **k: {tmp_path / "gen.c"})
        monkeypatch.setattr(cli, "unconverged_reasons", lambda *a, **k: [])
        monkeypatch.setattr(
            watch, "watch_and_build", lambda *a, **k: captured.update(k) or 0
        )
        (build_dir / "build.ninja").parent.mkdir(parents=True, exist_ok=True)
        (build_dir / "build.ninja").write_text("")

        # A build reports where it ran, which is how the watch learns the
        # directory the build script actually chose.
        assert (
            cli._watch(
                build=lambda: (0, build_dir), script=script, targets=[], ninja=None
            )
            == 0
        )
        return captured

    def test_build_directory_is_excluded(self, tmp_path: Path, monkeypatch) -> None:
        build_dir = tmp_path / "build"
        captured = self._run(monkeypatch, tmp_path, build_dir)

        assert captured["excluded_dirs"] == [build_dir]

    def test_in_source_build_excludes_no_directory(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """With -B ., excluding the build dir would exclude the project."""
        captured = self._run(monkeypatch, tmp_path, tmp_path)

        assert captured["excluded_dirs"] == []

    def test_ninja_outputs_are_excluded(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._run(monkeypatch, tmp_path, tmp_path / "build")

        assert tmp_path / "gen.c" in captured["excluded_paths"]

    def test_unconverged_build_is_reported(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        from pcons import cli

        cli._warn_unconverged(["output declared.txt doesn't exist"])

        assert "did not converge" in caplog.text
        assert "declared.txt" in caplog.text

    def test_a_converged_build_says_nothing(self, caplog) -> None:
        from pcons import cli

        cli._warn_unconverged([])

        assert caplog.text == ""

    def test_only_the_first_few_reasons_are_listed(self, caplog) -> None:
        from pcons import cli

        cli._warn_unconverged([f"reason {i}" for i in range(9)], limit=2)

        assert "reason 0" in caplog.text
        assert "reason 8" not in caplog.text
        assert "and 7 more" in caplog.text


class TestBuildDispatch:
    """`pcons build` picks the tool that matches the generated files."""

    @staticmethod
    def _build(build_dir: Path, **overrides) -> int:
        """One build, with nothing to regenerate unless a test says otherwise."""
        from pcons import cli

        overrides.setdefault("regenerate", lambda: (0, None))
        code, _where = cli._build(build_dir, **overrides)
        return code

    @staticmethod
    def _no_regeneration(monkeypatch) -> None:
        from pcons import cli

        monkeypatch.setattr(cli, "_needs_generation", lambda *a, **k: False)

    def test_runs_ninja_for_a_ninja_build(self, tmp_path: Path, monkeypatch) -> None:
        from pcons import cli

        self._no_regeneration(monkeypatch)
        (tmp_path / "build.ninja").write_text("")
        ran = []
        monkeypatch.setattr(cli, "run_ninja", lambda *a, **k: ran.append("ninja") or 0)

        assert self._build(tmp_path) == 0
        assert ran == ["ninja"]

    def test_runs_make_for_a_makefile_build(self, tmp_path: Path, monkeypatch) -> None:
        from pcons import cli

        self._no_regeneration(monkeypatch)
        (tmp_path / "Makefile").write_text("")
        ran = []
        monkeypatch.setattr(cli, "run_make", lambda *a, **k: ran.append("make") or 0)

        assert self._build(tmp_path) == 0
        assert ran == ["make"]

    def test_runs_xcodebuild_for_an_xcode_build(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons import cli

        self._no_regeneration(monkeypatch)
        (tmp_path / "demo.xcodeproj").mkdir()
        seen: dict = {}
        monkeypatch.setattr(cli, "run_xcodebuild", lambda *a, **k: seen.update(k) or 0)

        assert self._build(tmp_path, variant="debug") == 0
        assert seen["configuration"] == "debug"

    def test_stale_build_files_are_regenerated_first(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons import cli

        (tmp_path / "pcons-build.py").write_text("")
        (tmp_path / "build.ninja").write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_needs_generation", lambda *a, **k: True)
        order = []
        monkeypatch.setattr(
            cli, "run_ninja", lambda *a, **k: order.append("build") or 0
        )

        regenerated = SimpleNamespace(build_dir=tmp_path)
        assert (
            self._build(
                tmp_path,
                regenerate=lambda: order.append("generate") or (0, regenerated),
            )
            == 0
        )
        assert order == ["generate", "build"]

    def test_a_regeneration_that_describes_no_build_skips_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons import cli

        (tmp_path / "pcons-build.py").write_text("")
        (tmp_path / "build.ninja").write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_needs_generation", lambda *a, **k: True)
        order = []
        monkeypatch.setattr(
            cli, "run_ninja", lambda *a, **k: order.append("build") or 0
        )

        assert self._build(tmp_path, regenerate=lambda: (0, None)) == 0
        assert order == []

    def test_a_failed_regeneration_stops_the_build(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons import cli

        (tmp_path / "pcons-build.py").write_text("")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "_needs_generation", lambda *a, **k: True)
        monkeypatch.setattr(
            cli, "run_ninja", lambda *a, **k: pytest.fail("should not build")
        )

        assert self._build(tmp_path, regenerate=lambda: (1, None)) == 1

    def test_no_build_files_is_an_error(self, tmp_path: Path, monkeypatch) -> None:

        self._no_regeneration(monkeypatch)

        assert self._build(tmp_path) == 1

    def test_targets_reach_the_build_tool(self, tmp_path: Path, monkeypatch) -> None:
        from pcons import cli

        self._no_regeneration(monkeypatch)
        (tmp_path / "build.ninja").write_text("")
        seen: dict = {}
        monkeypatch.setattr(cli, "run_ninja", lambda *a, **k: seen.update(k) or 0)

        self._build(tmp_path, targets=["hello"])

        assert seen["targets"] == ["hello"]

    def test_named_build_script_is_preferred(self, tmp_path: Path) -> None:
        from pcons import cli

        script = tmp_path / "custom-build.py"

        assert cli._resolve_build_script(script) == script

    def test_interrupt_before_the_watch_starts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Ctrl-C during the very first build exits without a traceback."""
        from pcons import cli

        def interrupted() -> tuple[int, Path]:
            raise KeyboardInterrupt

        monkeypatch.setattr(watch, "ensure_available", lambda: None)

        assert cli._watch(build=interrupted, script=None, targets=[], ninja=None) == 0


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

    @staticmethod
    def _fake_ninja(
        monkeypatch, stdout: str = "", stderr: str = "", returncode: int = 0
    ):
        """Stand in for a ninja run, so the queries are tested without one."""
        import subprocess

        from pcons import cli

        monkeypatch.setattr(cli, "_find_ninja", lambda runner=None: ["ninja"])
        monkeypatch.setattr(
            cli.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], returncode, stdout, stderr
            ),
        )

    def test_outputs_come_from_ninja(self, tmp_path: Path, monkeypatch) -> None:
        from pcons.cli import ninja_outputs

        self._fake_ninja(monkeypatch, stdout="obj/main.c.o: cc\nall: phony\n")

        assert ninja_outputs(tmp_path) == {tmp_path / "obj" / "main.c.o"}

    def test_no_outputs_when_ninja_fails(self, tmp_path: Path, monkeypatch) -> None:
        """A build directory ninja cannot read is not a reason to fall over."""
        from pcons.cli import ninja_outputs

        self._fake_ninja(monkeypatch, stdout="obj/main.c.o: cc\n", returncode=1)

        assert ninja_outputs(tmp_path) == set()

    def test_no_outputs_without_ninja(self, tmp_path: Path, monkeypatch) -> None:
        from pcons import cli

        monkeypatch.setattr(cli, "_find_ninja", lambda runner=None: None)

        assert cli.ninja_outputs(tmp_path) == set()

    def test_a_converged_build_reports_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from pcons.cli import unconverged_reasons

        self._fake_ninja(monkeypatch, stdout="ninja: no work to do.\n")

        assert unconverged_reasons(tmp_path) == []

    def test_remaining_work_is_reported_with_ninjas_reason(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The silent failure: a command that never creates its output."""
        from pcons.cli import unconverged_reasons

        self._fake_ninja(
            monkeypatch,
            stdout="[1/1] COMMAND declared.txt\n",
            stderr="ninja explain: output declared.txt doesn't exist\n",
        )

        assert unconverged_reasons(tmp_path) == ["output declared.txt doesn't exist"]

    def test_a_failing_probe_is_not_a_finding(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """No ninja build to ask about (make, xcode) reports nothing."""
        from pcons.cli import unconverged_reasons

        self._fake_ninja(monkeypatch, stderr="ninja: error: loading", returncode=1)

        assert unconverged_reasons(tmp_path) == []

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

        def refuse() -> tuple[int, Path]:
            pytest.fail("should not build")

        assert cli._watch(build=refuse, script=None, targets=[], ninja=None) == 1


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
