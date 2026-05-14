from pcons import program, static_library

bb = static_library("bb").add_sources(["bb.cppm"])

program("bb_app").add_sources(["bb_main.cpp"]).link(bb)
