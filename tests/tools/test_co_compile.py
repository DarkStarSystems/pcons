# SPDX-License-Identifier: MIT
"""co_compile: run an analyzer alongside a compile, then compile."""

import sys

import pytest

from pcons.tools.co_compile import (
    Analyzers,
    iwyu_command,
    main,
    split_argv,
    tidy_command,
)

COMPILE = ["clang++", "-O2", "-MD", "-MF", "out.o.d", "-c", "-o", "out.o", "src/a.cc"]


class TestSplitArgv:
    def test_options_then_command(self):
        analyzers, cmd = split_argv(
            ["--tidy", "ct", "--tidy-arg", "--use-color", "--", "cc", "-c", "a.c"]
        )
        assert analyzers == Analyzers(tidy="ct", tidy_args=["--use-color"])
        assert cmd == ["cc", "-c", "a.c"]

    def test_both_analyzers(self):
        analyzers, _ = split_argv(
            ["--iwyu", "iw", "--iwyu-arg", "-Xiwyu", "--tidy", "ct", "--", "cc"]
        )
        assert analyzers == Analyzers(tidy="ct", iwyu="iw", iwyu_args=["-Xiwyu"])

    def test_no_analyzer_is_allowed(self):
        assert split_argv(["--", "cc", "a.c"]) == (Analyzers(), ["cc", "a.c"])

    def test_missing_separator_is_an_error(self):
        with pytest.raises(SystemExit, match="missing"):
            split_argv(["--tidy", "ct"])

    def test_unknown_option_is_an_error(self):
        with pytest.raises(SystemExit, match="unknown option"):
            split_argv(["--nope", "--", "cc"])


class TestAnalyzerCommand:
    def test_source_and_flags_are_split_around_the_separator(self):
        cmd = tidy_command("ct", ["--use-color"], COMPILE)
        assert cmd is not None
        assert cmd[:2] == ["ct", "--use-color"]
        sep = cmd.index("--")
        assert cmd[2:sep] == ["src/a.cc"]
        assert "-O2" in cmd[sep + 1 :]

    def test_output_and_depfile_flags_never_reach_the_analyzer(self):
        """Otherwise clang-tidy argues about an output it will not write, or
        overwrites the .d the real compile is about to produce."""
        cmd = tidy_command("ct", [], COMPILE)
        assert cmd is not None
        for dropped in ("-o", "out.o", "-MD", "-MF", "out.o.d", "-c"):
            assert dropped not in cmd[cmd.index("--") + 1 :]

    def test_the_compiler_itself_is_not_a_flag(self):
        cmd = tidy_command("ct", [], COMPILE)
        assert cmd is not None
        assert "clang++" not in cmd

    def test_an_inner_launcher_stays_out_of_the_analysis(self):
        """A compiler cache wrapping the compile is not a flag."""
        cmd = tidy_command("ct", [], ["ccache", *COMPILE])
        assert cmd is not None
        flags = cmd[cmd.index("--") + 1 :]
        assert "ccache" not in flags
        assert "clang++" not in flags
        assert flags == ["-O2"]

    def test_an_msvc_style_compile_is_read_in_cl_mode(self):
        """cl.exe's flags start with a slash and carry outputs joined on;
        clang-tidy must parse them as clang-cl would, or it sees no
        includes and defines at all."""
        compile_cmd = [
            "cl.exe",
            "/nologo",
            "/showIncludes",
            "/c",
            "/Foout.obj",
            "/Iinc",
            "/DX=1",
            "src/a.cc",
        ]
        cmd = tidy_command("ct", [], compile_cmd)
        assert cmd is not None
        sep = cmd.index("--")
        assert cmd[:sep] == ["ct", "--extra-arg-before=--driver-mode=cl", "src/a.cc"]
        assert cmd[sep + 1 :] == ["/nologo", "/Iinc", "/DX=1"]

    def test_no_source_means_nothing_to_analyze(self):
        """A link command carries no source; there is nothing to hand over."""
        assert tidy_command("ct", [], ["clang++", "-o", "app", "a.o"]) is None


class TestIwyuCommand:
    def test_flags_then_source_after_its_own_options(self):
        cmd = iwyu_command("iw", ["-Xiwyu", "--verbose=3"], COMPILE)
        assert cmd == ["iw", "-Xiwyu", "--verbose=3", "-O2", "src/a.cc"]

    def test_an_msvc_style_compile_is_read_in_cl_mode(self):
        cmd = iwyu_command("iw", [], ["cl.exe", "/c", "/Foa.obj", "/Iinc", "a.cc"])
        assert cmd == ["iw", "--driver-mode=cl", "/Iinc", "a.cc"]


class TestMain:
    def test_compiles_and_reports_success(self, tmp_path):
        out = tmp_path / "made"
        rc = main(["--", sys.executable, "-c", f"open({str(out)!r}, 'w').close()"])
        assert rc == 0
        assert out.exists()

    def test_analyzer_failure_fails_the_build_but_still_compiles(self, tmp_path):
        """The object is still wanted, and the diagnostic must repeat next time
        rather than becoming a missing-file error downstream."""
        out = tmp_path / "made"
        rc = main(
            [
                "--tidy",
                sys.executable,
                "--tidy-arg",
                "-c",
                "--tidy-arg",
                "raise SystemExit(3)",
                "--",
                sys.executable,
                "-c",
                f"open({str(out)!r}, 'w').close()",
                "a.cc",  # a source, so there is something to analyze
            ]
        )
        assert rc != 0
        assert out.exists(), "the compile must run even when the analysis failed"

    @pytest.mark.parametrize(("args", "fails"), [([], False), (["--error"], True)])
    def test_iwyu_fails_the_build_only_when_asked(self, tmp_path, args, fails):
        """Older include-what-you-use exits nonzero whatever it found."""
        out = tmp_path / "made"
        fake_iwyu = ["--iwyu", sys.executable, "--iwyu-arg", "-c"]
        fake_iwyu += ["--iwyu-arg", "raise SystemExit(2)"]
        fake_iwyu += [a for arg in args for a in ("--iwyu-arg", arg)]
        rc = main(
            [
                *fake_iwyu,
                "--",
                sys.executable,
                "-c",
                f"open({str(out)!r}, 'w').close()",
                "a.cc",
            ]
        )
        assert (rc != 0) is fails
        assert out.exists()

    def test_compile_failure_wins_over_a_clean_analysis(self):
        rc = main(["--", sys.executable, "-c", "raise SystemExit(7)"])
        assert rc == 7

    def test_empty_command_is_an_error(self):
        with pytest.raises(SystemExit, match="empty compile command"):
            main(["--"])
