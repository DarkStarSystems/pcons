from pcons import context

project = context.current_project
env = project.default_environment

bb = project.StaticLibrary("bb", env, sources=["bb.cppm"])

project.Program("bb_app", env, sources=["bb_main.cpp"]).link(bb)
