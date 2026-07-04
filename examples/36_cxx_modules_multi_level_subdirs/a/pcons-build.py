from pcons import add_subdirectory, context

project = context.current_project
env = project.default_environment
a = project.StaticLibrary("a", env, sources=["a.cppm"])

# pick "aa" variable ("libaa") from the subdirectory aa/pcons-build.py
(aa,) = add_subdirectory("aa", pick=["aa"])

a_app = project.Program("a_app", env, sources=["a_main.cpp"])
a_app.link_private(a, aa)
