from pcons import add_subdirectory, context, get_target

project = context.current_project
env = project.default_environment
a = project.StaticLibrary("a", env, sources=["a.cppm"])
add_subdirectory("aa")

project.Program("a_app", env, sources=["a_main.cpp"]).link(a, get_target("aa"))
