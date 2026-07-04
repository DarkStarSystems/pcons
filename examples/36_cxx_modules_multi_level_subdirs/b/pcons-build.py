from pcons import add_subdirectory, context

project = context.current_project
env = project.default_environment
b = project.StaticLibrary("b", env, sources=["b.cppm"])

# load the subdirectory's pcons-build.py as SimpleNamespace (with all variables defined in it)
s = add_subdirectory("bb")

b_app = project.Program("b_app", env, sources=["b_main.cpp"])
b_app.link_private(b, s.bb)
