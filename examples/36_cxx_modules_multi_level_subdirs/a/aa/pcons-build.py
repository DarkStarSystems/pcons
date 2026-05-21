from pcons import context

project = context.current_project
env = project.default_environment

aa = project.StaticLibrary("aa", env, sources=["aa.cppm"])
