# Example: Paths with Spaces

This example demonstrates that pcons correctly handles paths and values containing spaces:

- **Source directory with spaces**: `src with spaces/`
- **Source filename with spaces**: `my program.c`
- **Include directory with spaces**: `My Headers/`
- **Define with spaces in value**: `GREETING_MESSAGE="Hello from pcons!"`

## Why This Matters

Many systems have paths with spaces:
- Windows: `C:\Program Files\`, `C:\Users\John Doe\`
- macOS with iCloud: `~/Library/Mobile Documents/`
- Any user-created folder: `My Projects/`, `Source Code/`

Build systems must properly escape or quote these paths when generating:
- Ninja build files (spaces escaped as `$ `)
- Makefiles (shell quoting)
- compile_commands.json (for IDE integration)

## Building

```bash
python pcons-build.py
cd build
ninja
./my_program
```

## Expected Output

```
Program built from path with spaces!
Hello from pcons!
```

## Generated Ninja File

The generated `build.ninja` uses proper escaping and quoting:

```ninja
# Output paths are relative to the build directory
# Source paths use Ninja's $ escaping for spaces
build obj.my_program/my$ program.o: cc_objcmd /path/to/src$ with$ spaces/my$ program.c
  in = /path/to/src with spaces/my program.c
  out = obj.my_program/my program.o
  includes = '-I/path/to/My Headers'
  defines = '-DGREETING_MESSAGE="Hello from pcons!"'
```

Key points:
- Output paths (`build obj.my_program/...`) use Ninja `$ ` escaping for build graph
- Variable values (`includes`, `defines`) use shell quoting for command execution
- The `in` variable contains the unescaped path for shell commands
