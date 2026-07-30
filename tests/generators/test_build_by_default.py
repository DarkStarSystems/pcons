# SPDX-License-Identifier: MIT
"""Tests for Target.build_by_default: utility targets (lupdate, doc
generation, ...) are excluded from 'all' and implicit defaults, in both
generators, but still buildable by name and includable via Default()."""

from __future__ import annotations

import sys

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator


@pytest.fixture
def project_with_utility(tmp_path, monkeypatch):
    """A project with a normal command target and a utility target."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in.txt").write_text("x\n")
    project = Project("bbd", root_dir=tmp_path, build_dir=tmp_path / "build")
    env = project.Environment()
    python = sys.executable.replace("\\", "/")
    copy = [python, "-c", "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])"]
    env.Command(
        target="normal.out",
        source="in.txt",
        command=[*copy, "$SOURCE", "$TARGET"],
        name="normal",
    )
    utility = env.Command(
        target="utility.out",
        source="in.txt",
        command=[*copy, "$SOURCE", "$TARGET"],
        name="utility",
    )
    utility.build_by_default = False
    project.Alias("util", utility)
    return project


def _generate(project, generator) -> str:
    generator.generate(project)
    BaseGenerator._generate_pending(project)
    name = "build.ninja" if isinstance(generator, NinjaGenerator) else "Makefile"
    return (project.build_dir / name).read_text().replace("\\", "/")


class TestNinja:
    def test_excluded_from_all_and_default(self, project_with_utility):
        content = _generate(project_with_utility, NinjaGenerator())
        for line in content.splitlines():
            if line.startswith("build all: phony"):
                assert "utility.out" not in line
                assert "normal.out" in line
            if line.startswith("default "):
                # Either explicit outputs or "default all"; both exclude
                # the utility (all itself is filtered above).
                assert "utility.out" not in line
        # The edge itself exists (buildable via `ninja util`).
        assert "build utility.out:" in content
        assert "build util: phony" in content


class TestMakefile:
    def test_excluded_from_all_and_default(self, project_with_utility):
        content = _generate(project_with_utility, MakefileGenerator())
        for line in content.splitlines():
            if line.startswith("all:") or line.startswith(".DEFAULT_GOAL"):
                assert "utility.out" not in line
        assert "utility.out:" in content
