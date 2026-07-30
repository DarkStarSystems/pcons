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

    def test_lupdate_excluded_from_default_and_all(self, tr_project):
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
        # ...but neither 'all' nor the default line includes it.
        for line in content.splitlines():
            if line.startswith("build all: phony") or line.startswith("default "):
                assert "lupdate" not in line
