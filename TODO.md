# pcons TODO - Features needed for markymark build

## Missing Features Identified from markymark Analysis

### High Priority (Required for markymark)

1. **Build Variants (debug/release/profile)**
   - Currently no way to define and switch between build configurations
   - Need: different compiler flags, defines, output directories per variant
   - SCons approach: `variants` variable, creates separate build dirs

2. **Platform Detection**
   - Need to detect OS (Windows/macOS/Linux) and configure accordingly
   - Different compilers, flags, library paths per platform
   - pcons has some platform code but not exposed to build scripts

3. **CUDA Toolchain**
   - .cu file compilation with nvcc
   - CUDA library linking (cudart, npp*, etc.)
   - GPU architecture flags

4. **External Dependency Finding**
   - FindOpenCV, FindCUDA equivalent
   - Library path detection
   - Include path detection
   - pkg-config integration?

5. **Custom Commands/Actions**
   - Run arbitrary commands during build (resource compilation, bundle creation)
   - File copying, directory creation
   - Post-build steps

6. **Source Globbing**
   - `project.glob("src/*.cpp")` to find source files
   - Exclude patterns

7. **Install Targets**
   - Copy files to install locations
   - Create directory structures (bundles)

### Medium Priority

8. **Compiler Flag Configuration**
   - Easy way to set C++ standard (C++20)
   - Optimization levels
   - Warning flags
   - Platform-specific flags

9. **Preprocessor Defines per Target**
   - Already have target.private.defines but need to verify it works

10. **RPATH/Install Name Configuration**
    - macOS @loader_path, @rpath
    - Linux $ORIGIN
    - Windows manifest embedding

### Lower Priority (Nice to have)

11. **Conan Integration**
    - Package manager support

12. **Windows Resource Compilation**
    - .rc files for version info

13. **macOS Rez Compilation**
    - Resource files for AE plugins

## Architecture Notes

markymark structure:
- Single main library compiled from ~10 .cxx files + 1 .cu file
- Three "ports": OFX, AE, Spark (different plugin interfaces)
- Each port uses same core code with different main entry point
- Plugins are shared libraries with platform-specific bundling

Proposed pcons approach:
- Core library as StaticLibrary (shared code)
- Each port as SharedLibrary linking core
- Custom commands for bundle creation
- Variant support via environment cloning or project variants

## Implementation Plan

Phase 1: Basic markymark build (OFX only, single variant)
- Verify C++ compilation works
- Add source globbing
- Get basic shared library building

Phase 2: Add variants
- Design variant system
- Implement debug/release switching

Phase 3: Add CUDA
- CUDA toolchain
- .cu file handling

Phase 4: Full markymark
- All three ports
- Bundle creation
- Install targets
