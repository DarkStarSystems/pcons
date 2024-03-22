from pcons.node import FileNode
from pcons.project import Project
from pcons.generator import NinjaGenerator

# Here's an example of what a trivial build setup could look like:

def test_trivial_example(tmpdir):
    srcfile = tmpdir.join('src.cxx')
    srcfile.write("""
    int main(int argc, char **argv) { return 0; }
    """)
    targetfile = tmpdir.join('foo')

    p = Project("Test Project", generator=NinjaGenerator())
    toolchain = p.addToolchain(toolchain='c++')
    # toolchain.setConfig('Debug')
    t = p.target(targetfile)
    t.depends([srcfile])
    ninjafile = tmpdir.join('build.ninja')
    p.generate(ninjafile)
    with open(ninjafile, 'r') as f:
        text = f.read()
        print(f'Ninjafile: {text}')
        assert(text.startswith("# Ninja build script"))
