import pathlib
from pcons.node import Node, FSNode, FileNode

def test_node():
    n = Node()
    assert(n.explicit_deps == [])
    assert(n.explicit_deps == [])

def test_fsnode():
    n = FSNode("/tmp/foo.bar")
    assert(n.path == pathlib.Path("/tmp/foo.bar"))
    assert(n.explicit_deps == [])
    assert(n.explicit_deps == [])

def test_depends():
    n1 = FSNode("/tmp/target")
    n2 = FSNode("/tmp/source")
    n1.depends(n2)
    assert(len(n1.deps()) > 0)
    assert(len(n2.deps()) == 0)
    assert(n1.deps()[0] == n2)

def test_file(tmpdir):      # tmpdir test module objects are LocalPath
    d = tmpdir.mkdir('sub')
    f = d.join('src.txt')
    f.write('test file')
    n1 = FileNode(f)
    n2 = FileNode(d.join('target.txt'))
    n2.depends(n1)
    assert(n1.exists())
    assert(not n2.exists())
