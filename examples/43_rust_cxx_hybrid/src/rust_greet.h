// Hand-written C header for the rust_greet staticlib.
//
// Kept hand-written for this example so it has no extra toolchain
// dependencies (no cbindgen, no bindgen). pcons.tools.cargo.CargoBuild
// supports auto-generating this via cbindgen if you point it at a
// cbindgen.toml — see its docstring.

#ifndef RUST_GREET_H
#define RUST_GREET_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

int32_t rust_greet_sum(int32_t a, int32_t b);

int64_t rust_greet_message(const char* name, char* out, size_t cap);

#ifdef __cplusplus
}
#endif

#endif
