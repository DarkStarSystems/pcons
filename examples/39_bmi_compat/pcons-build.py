from pcons import Project, find_c_toolchain, get_var

project = Project("bmi-compat")


toolchain_override = get_var("TOOLCHAIN")
if toolchain_override:
    toolchain = find_c_toolchain(prefer=[toolchain_override])
else:
    toolchain = find_c_toolchain(prefer=["gcc", "llvm", "msvc"])

env = project.Environment(toolchain=toolchain)

# lib1 - reference library
lib1 = project.StaticLibrary(
    "lib1",
    env,
    sources=["provider.cppm", "consumer.cpp"],
)
lib1.private.compile_flags.append("-std=c++23")

# lib2 - provider.bmi should point to the same BMI as lib1 (same flags)
# status:
#  - GCC: OK, uses gcm.cache/provider.gcm
#  - Clang: OK, uses cxx_modules/provider.gcm
#  - MSVC: ???
lib2 = project.StaticLibrary(
    "lib2",
    env,
    sources=["provider.cppm", "consumer.cpp"],
)
lib2.private.compile_flags.append("-std=c++23")


# lib3 - provider.bmi should point to a NEW BMI (c++26 is BMI breaker)
# status:
#  - GCC: does not compile (multiple rules generate gcm.cache/provider.gcm)
#  - Clang: multiple rules generate cxx_modules/provider.pcm
#  - MSVC: ???
lib3 = project.StaticLibrary(
    "lib3",
    env,
    sources=["provider.cppm", "consumer.cpp"],
)
lib3.private.compile_flags.append("-std=c++26")
