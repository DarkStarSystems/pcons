# SPDX-License-Identifier: MIT
"""One project, two environments, one library built for both.

Each environment owns a slice of the build directory, so the same library and
the same program can be declared once per environment without renaming
anything:

    build/host/lib/libchecksum.a    build/host/bin/app
    build/strict/lib/libchecksum.a  build/strict/bin/app

What it shows:

- `env.build_prefix`, holding everything an environment writes, object files
  included.
- `env.archive_directory` and `env.runtime_directory`, placing the artifacts by
  kind below it. The toolchain still decides "lib", ".a" and ".exe".
- Two targets sharing a name, told apart by their environments.
- `add_subdirectory(env=...)`, which includes one directory once per
  environment: `parity/` is described once and built for both, and its script
  never names an environment.
- `checksum@host`, the spelling that names one of them, read by
  `project.get_target`, by `project.Default` and by `pcons build`. A link
  string stays a raw link token, so the target is passed as an object and
  `link("m")` still means `-lm`.

Not the same as example 66_multi_project, which is two top-level projects with
two build directories and no edges between them, nor 34_multi_build_dir, which
builds one variant per pcons run. This is one build, described once, targeting
two environments at the same time.
"""

from pcons import Project, add_subdirectory

project = Project("multi_env")

host = project.Environment(toolchain="c", name="host")
host.build_prefix = "host"
host.archive_directory = "lib"
host.runtime_directory = "bin"

strict = project.Environment(toolchain="c", name="strict")
strict.build_prefix = "strict"
strict.archive_directory = "lib"
strict.runtime_directory = "bin"
strict.cc.defines.append("STRICT")


parity = {
    env.name: add_subdirectory("parity", env=env).parity for env in (host, strict)
}

for env in (host, strict):
    lib = project.StaticLibrary("checksum", env, sources=["src/checksum.c"])
    lib.public.include_dirs.append(project.root_dir / "src")

for env in (host, strict):
    app = project.Program("app", env, sources=["src/main.c"])
    app.link(project.get_target(f"checksum@{env.name}"))
    app.link(parity[env.name])

project.Default("app@host", "app@strict")
