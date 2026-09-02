# SPDX-License-Identifier: MIT
"""Tests for QtTranslations (no Qt installation required)."""

from __future__ import annotations

import pytest

from pcons.core.project import Project

from ._qt_test_utils import cxx_env_with_qt, generate_ninja


@pytest.fixture
def tr_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n")
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "app_de.ts").write_text("<TS/>\n")
    (tmp_path / "i18n" / "app_fr.ts").write_text("<TS/>\n")
    return Project("trtest", root_dir=tmp_path, build_dir=tmp_path / "build")


class TestQtTranslations:
    def test_lrelease_and_embed(self, tr_project, tmp_path):
        env = cxx_env_with_qt(tr_project)
        tr_project.QtTranslations(
            "i18n", env, ts_files=["i18n/app_de.ts", "i18n/app_fr.ts"]
        )
        content = generate_ninja(tr_project)

        assert "/fake/bin/lrelease" in content
        assert "build qt.i18n/app_de.qm: qt_lreleasecmd" in content
        assert "build qt.i18n/app_fr.qm: qt_lreleasecmd" in content
        assert "-qm $out" in content
        assert "build qt.i18n/qrc_i18n.cpp: qt_rcccmd" in content

        qrc = (tmp_path / "build" / "qt.i18n" / "i18n.qrc").read_text()
        assert '<qresource prefix="/i18n">' in qrc
        assert 'alias="app_de.qm"' in qrc

    def test_empty_ts_files_is_an_error(self, tr_project):
        env = cxx_env_with_qt(tr_project)
        with pytest.raises(ValueError, match="ts_files is empty"):
            tr_project.QtTranslations("i18n", env, ts_files=[])

    def test_lupdate_is_manual(self, tr_project):
        env = cxx_env_with_qt(tr_project)
        tr_project.QtTranslations(
            "i18n",
            env,
            ts_files=["i18n/app_de.ts"],
            lupdate_sources=["src/main.cpp"],
        )
        tr_project.Program("app", env, sources=["src/main.cpp"])
        content = generate_ninja(tr_project)

        # The lupdate edge exists and is reachable via the alias...
        assert "i18n-lupdate.stamp" in content
        assert "build lupdate: phony" in content
        # ...but the manual tier keeps it out of 'all' and the default line.
        for line in content.splitlines():
            if line.startswith("build all: phony") or line.startswith("default "):
                assert "lupdate" not in line


class TestTranslationsEnvironment:
    """Translations keep the environment they were declared in."""

    def test_one_name_in_two_environments(self, tr_project):
        host = cxx_env_with_qt(tr_project, name="host")
        cross = cxx_env_with_qt(tr_project, name="cross")
        before = len(tr_project.environments)

        on_host = tr_project.QtTranslations("i18n", host, ts_files=["i18n/app_de.ts"])
        on_cross = tr_project.QtTranslations("i18n", cross, ts_files=["i18n/app_de.ts"])

        assert on_host.qualified_name == "trtest::i18n@host"
        assert on_cross.qualified_name == "trtest::i18n@cross"
        assert len(tr_project.environments) == before


CHILD_TRANSLATIONS_SCRIPT = """\
# SPDX-License-Identifier: MIT
from pcons.core.project import Project

project = Project("child")
project.QtTranslations(
    "i18n", project.default_environment, ts_files=["i18n/app_de.ts"]
)
"""


class TestTranslationsInASubdirectory:
    """The .qrc a child script generates must land where its edge names it.

    ninja resolves an edge's paths against the top-level build directory,
    which is where node paths are anchored too. A sub-project's own
    ``root_dir`` is a directory the generated build files never mention, so
    a file written there is a file no rule knows how to make.
    """

    def test_the_generated_qrc_is_the_file_the_rcc_edge_reads(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        child = tmp_path / "child"
        (child / "i18n").mkdir(parents=True)
        (child / "i18n" / "app_de.ts").write_text("<TS/>\n")
        (child / "pcons-build.py").write_text(CHILD_TRANSLATIONS_SCRIPT)

        project = Project("top", root_dir=tmp_path, build_dir=tmp_path / "build")
        cxx_env_with_qt(project)
        project.add_subdirectory("child")
        content = generate_ninja(project)

        edge = next(
            line
            for line in content.splitlines()
            if line.startswith("build ") and "/qrc_i18n.cpp:" in line
        )
        inputs = edge.split(":", 1)[1].split("|", 1)[0].split()[1:]
        assert len(inputs) == 1
        assert 'alias="app_de.qm"' in (tmp_path / "build" / inputs[0]).read_text()
        assert inputs == ["child/qt.i18n/i18n.qrc"]
