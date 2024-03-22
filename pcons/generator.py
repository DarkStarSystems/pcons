# SPDX-License-Identifier: MIT

from pathlib import Path

class Generator():
    import pcons.project
    def generate(self, project: 'pcons.project.Project', file: Path):
        raise NotImplementedError("No generate() for base Generator")

class NinjaGenerator(Generator):
    import pcons.project
    def generate(self, project: 'pcons.project.Project', file: Path):
        with file.open('w') as f:
            f.write(f'# Ninja build script for {project}')
