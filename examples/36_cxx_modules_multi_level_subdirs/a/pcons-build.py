from pcons import add_subdirectory, get_target, program, static_library

a = static_library("a", sources=["a.cppm"])
add_subdirectory("aa")

program("a_app", sources=["a_main.cpp"]).link(a, get_target("aa"))
