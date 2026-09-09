#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bare-metal Cortex-M3 firmware, and the host tools that go with it.

A firmware build needs two toolchains at once: the cross compiler that
produces the image, and the host compiler that builds the code generators and
tests that run on the build machine. This example uses both in one project.

What it shows:

1. Retargeting GCC onto cross binaries with a CrossPreset whose tool_cmds
   name the arm-none-eabi-* tools. GCC picks its target by binary, not by a
   --target flag, so the preset carries the commands.
2. Each environment owning its slice of the build directory: build_prefix
   holds everything it writes, archive_directory and runtime_directory place
   the artifacts by kind below it. The toolchain still decides "lib" and ".a".
3. Freestanding compile and link flags, mixed C and C++, no libc, no libstdc++.
4. A static library (a board-support layer) archived with arm-none-eabi-ar.
5. One library built for both worlds from a function of the environment: the
   checksum the firmware speaks on the wire is compiled freestanding for the
   MCU and again with the host compiler, so a host program can test the
   algorithm before anything is flashed. Both are named "common": a target is
   identified by its name and its environment, and "common@target" is how one
   is named, to pcons or on the command line.
6. A host-built generator producing a header the firmware includes.
7. A linker script passed inside a -T flag, wrapped in PathToken so the
   generator relativizes it, plus an implicit dep so editing it relinks.
8. Post-link artifacts: .bin, .hex and a size report, as env.Command steps.
   These land under the cross environment's prefix too, since build_prefix
   covers everything an environment writes, not only what it links.
9. A user command, `pcons run qemu`, that names the firmware as a dependency
   and so builds it before booting it.

The firmware targets QEMU's lm3s6965evb board so the result is executable: it
writes to UART0 and exits through semihosting.

    pcons                    # build the firmware, the host tool and the host test
    pcons build common@host  # build one target of one environment
    pcons run qemu           # build the firmware if needed, then boot it

Needs arm-none-eabi-gcc, arm-none-eabi-binutils and qemu-system-arm. The
language standards are kept at C11/C++11 so the example builds with the ARM
toolchain the distributions ship, which trails the Arm GNU releases.
"""

from pcons import PathToken, Project, cli_command
from pcons.toolchains.presets import CrossPreset

CPU_FLAGS = ("-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft")

cortex_m3 = CrossPreset(
    name="cortex-m3-none-eabi",
    arch="armv7-m",
    triple="arm-none-eabi",
    extra_compile_flags=(
        *CPU_FLAGS,
        "-ffreestanding",
        "-ffunction-sections",
        "-fdata-sections",
    ),
    extra_link_flags=(
        *CPU_FLAGS,
        "-nostdlib",
        "-Wl,--gc-sections",
        "-Wl,-Map=target/firmware.map",
    ),
    tool_cmds={
        "cc": "arm-none-eabi-gcc",
        "cxx": "arm-none-eabi-g++",
        "link": "arm-none-eabi-gcc",
        "ar": "arm-none-eabi-ar",
    },
)

project = Project("bare_metal")

# The cross environment builds the firmware. The preset points the tools at
# the arm-none-eabi binaries and adds the freestanding Cortex-M3 flags, and
# build_prefix keeps everything it writes under build/target.
env = project.Environment(toolchain="gcc", name="target")
env.apply_cross_preset(cortex_m3)
env.build_prefix = "target"
env.archive_directory = "lib"

env.cc.flags += ["-Os", "-g3", "-std=c11", "-Wall", "-Wextra"]
env.cxx.flags += [
    "-Os",
    "-g3",
    "-std=c++11",
    "-Wall",
    "-Wextra",
    "-fno-exceptions",
    "-fno-rtti",
    "-fno-threadsafe-statics",
    "-fno-use-cxa-atexit",
]
env.cc.includes.append(project.root_dir / "src")
env.cxx.includes.append(project.root_dir / "src")

# The host environment builds what runs on the build machine.
host_env = project.Environment(toolchain="gcc", name="host")
host_env.build_prefix = "host"
host_env.archive_directory = "lib"
host_env.runtime_directory = "bin"

# The generated header is a host artifact the cross build consumes, so the
# include path follows the host environment's build directory.
env.cc.includes.append(host_env.build_dir / "gen")

genver = project.Program("genver", host_env, sources=["tools/genver.c"])

# cwd= makes $SOURCE expand to "build/host/bin/genver" rather than a bare
# "genver", which the shell would look up on PATH instead of executing.
version_h = host_env.Command(
    target="gen/version.h",
    source=[genver],
    command="$SOURCE $TARGET",
    cwd=project.root_dir,
    name="version-header",
)


# The checksum the firmware speaks on the wire, built twice from one source:
# freestanding for the MCU, and again with the host compiler so a host program
# can test the algorithm before anything is flashed.
#
# A plain function of the environment is all it takes. The two libraries share
# a name because their environments are named and write elsewhere, so nothing
# here says which environment it is for. The same function can vary sources or
# flags per environment.
def common_lib(environment):
    lib = project.StaticLibrary("common", environment, sources=["src/common.c"])
    lib.public.include_dirs.append(project.root_dir / "src")
    return lib


common = common_lib(env)
common_host = common_lib(host_env)

checksum_test = project.Program(
    "checksum_test", host_env, sources=["tools/checksum_test.c"]
)
checksum_test.link(common_host)

# Board support, archived with arm-none-eabi-ar.
bsp = project.StaticLibrary("bsp", env, sources=["src/uart.c", "src/startup.c"])
bsp.public.include_dirs.append(project.root_dir / "src")

fw = project.Program(
    "firmware",
    env,
    sources=["src/main.c", "src/blink.c", "src/banner.cpp"],
)
fw.link(bsp, common)
fw.output_suffix = ".elf"

# PathToken paths are relative to the project root, not the build dir.
fw.private.link_flags.append(
    PathToken(prefix="-T", path="link/lm3s6965evb.ld", path_type="project")
)
# Nothing in the program references the reset handler, so the archive member
# holding it (and the vector table) would never be pulled in. -u forces it.
fw.private.link_flags += ["-Wl,-u,Reset_Handler"]
# Relink when the linker script changes.
fw.depends("link/lm3s6965evb.ld")
fw.depends(version_h)

# name= is required: Command derives its target name from the output stem,
# which would collide with the Program named "firmware".
binary = env.Command(
    target="firmware.bin",
    source=[fw],
    command="arm-none-eabi-objcopy -O binary $SOURCE $TARGET",
    name="firmware-bin",
)

hexfile = env.Command(
    target="firmware.hex",
    source=[fw],
    command="arm-none-eabi-objcopy -O ihex $SOURCE $TARGET",
    name="firmware-hex",
)

sizereport = env.Command(
    target="firmware.size",
    source=[fw],
    command="arm-none-eabi-size $SOURCE > $TARGET",
    name="firmware-size",
)

project.Default(fw, binary, hexfile, sizereport, checksum_test)


@cli_command()
def qemu() -> None:
    """Run the firmware in QEMU."""
    import shutil

    qemu_bin = shutil.which("qemu-system-arm")
    if not qemu_bin:
        raise RuntimeError("qemu-system-arm not found on PATH")

    qemu_cmd = [
        qemu_bin,
        "-M",
        "lm3s6965evb",
        "-nographic",
        "-semihosting",
        "-kernel",
        str(fw.output_nodes[0].path),
    ]
    import subprocess

    subprocess.run(qemu_cmd, check=True)


qemu.depends(fw)
