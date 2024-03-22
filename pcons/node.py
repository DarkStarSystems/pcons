# SPDX-License-Identifier: MIT

# Node base class for all nodes: filesystem (source and/or target), value, or custom

import pathlib
from typing import List, Dict, Optional, Union
from enum import Enum


class NodeStatus(Enum):
    Unknown = "unknown"
    UpToDate = "uptodate"
    OutOfDate = "outofdate"


class Node:
    explicit_deps: list["Node"]
    implicit_deps: list["Node"]

    def __init__(self, **args):
        "Base class for a Node, an entry in the project dependency graph."
        self.explicit_deps = args.get("dependencies", [])
        self.implicit_deps = []

    def deps(self):
        """All direct dependencies of this node"""
        return self.explicit_deps + self.implicit_deps

    def depends(self, n: Union["Node", list["Node"]]) -> None:
        """Add one or more dependencies for this node, i.e. node(s) which must be up to date
        before we can build this one."""
        if isinstance(n, Node):
            self.explicit_deps.append(n)
        else:
            self.explicit_deps.extend(n)


class FSNode(Node):
    """A file system node, representing a possible object in the
    file system.

    Note that FSNode objects may or may not exist in
    the filesystem at the time this is called, for example if the
    FSNode represents a target to be built."""

    path: pathlib.Path

    def __init__(self, path: pathlib.Path | str, **_args):
        super().__init__()
        self.path = pathlib.Path(path)

    def exists(self) -> bool:
        return self.path.exists()


class FileNode(FSNode):
    """A file system File node, representing a possible file in the file system."""

    def __init__(self, path: pathlib.Path | str, **_args):
        super().__init__(path)

    @classmethod
    def fromFSNode(cls, fs_obj):
        """Create a File node from a generic FSNode"""
        n = cls(fs_obj.path)
        n.explicit_deps = fs_obj.explicit_deps
        n.implicit_deps = fs_obj.implicit_deps
