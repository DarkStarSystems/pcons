from pcons import add_subdirectory, get_target, program, static_library

b = static_library("b").add_sources(["b.cppm"])

add_subdirectory("bb")

program("b_app").add_sources(["b_main.cpp"]).link(b, get_target("bb"))
