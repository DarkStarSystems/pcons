from pcons import add_subdirectory, context

project = context.current_project
env = project.default_environment
b = project.StaticLibrary("b", env, sources=["b.cppm"])

add_subdirectory("bb")

project.Program("b_app", env, sources=["b_main.cpp"]).link(b, context.get_target("bb"))
