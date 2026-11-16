# SPDX-License-Identifier: MIT
"""Build script for a Windows installer compiled by Inno Setup.

Inno Setup builds a setup program from a script, hello.iss, which says
what to install where. create_inno_setup() stages the files the
installer packs, passes the script values to read as {#Name}, and runs
the compiler, ISCC.exe, as a build step: the installer rebuilds when the
program, the script or any file it reads changes.
"""

from pathlib import Path

from pcons import Project

project = Project("inno_setup_example")
env = project.Environment(toolchain="c")

app = project.Program("hello", env)
app.add_sources([project.root_dir / "src" / "main.c"])
project.Default(app)

if env.target.is_windows:
    from pcons.contrib.installers import windows

    version = "1.0.0"
    installer = windows.create_inno_setup(
        project,
        env,
        name="Hello",
        script="hello.iss",
        sources=[app],  # staged for the script as {#StagingDir}
        defines={"Version": version, "Readme": Path("readme.txt")},
        depends=["hello.ico"],  # the script's SetupIconFile
        output=f"Hello-{version}-setup.exe",
    )
    project.Default(installer)
