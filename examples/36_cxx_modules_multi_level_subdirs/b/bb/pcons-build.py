from pcons import context

project = context.current_project
env = project.default_environment

bb = project.StaticLibrary("bb", env, sources=["bb.cppm"])

bb_app = project.Program("bb_app", env, sources=["bb_main.cpp"])
bb_app.private.link_libs.extend([bb])
