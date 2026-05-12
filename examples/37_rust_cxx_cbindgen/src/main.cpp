// Consumes a cbindgen-generated C header for the rust_math staticlib.
// The header lives in build/cargo/rust_math/include/rust_math.h —
// pcons added that path to the include search via the imported
// target's public usage requirements.

#include "rust_math.h"

#include <cstdio>

int main() {
    double xs[] = {3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0, 5.0, 3.0};
    Stats s = rust_math_stats(xs, sizeof(xs) / sizeof(xs[0]));
    std::printf("min=%.1f max=%.1f mean=%.2f\n", s.min, s.max, s.mean);
    std::printf("clamp(7.5, 0, 5) = %.1f\n", rust_math_clamp(7.5, 0.0, 5.0));
    return 0;
}
