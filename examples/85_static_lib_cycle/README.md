# Two static libraries that need each other

`parser` calls `lexer_count()`, and `lexer` calls `parser_is_delim()` back.
Each library links the other:

```python
lexer.link(parser)
parser.link(lexer)
```

That is a dependency cycle, and for static libraries it is fine: neither is
built before the other, because compiling one needs only the other's
headers, and the linker resolves both archives together. `main` links
`parser` alone and gets `lexer` with it.

On Linux the link line wraps the two archives in a group, since GNU ld
searches each archive once:

```
gcc -o main obj.main/src/main.c.o -Wl,--start-group libparser.a liblexer.a -Wl,--end-group
```

Apple's ld and MSVC's link rescan archives on their own, so there the
archives are plain link inputs.

Only static libraries, object libraries and header-only libraries may be in
such a cycle. A program or shared library that links a library which links
it back is refused with a message that says which target is the problem.

```
$ pcons
$ ./build/main
3 tokens
```
