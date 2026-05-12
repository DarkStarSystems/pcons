#include "rust_greet.h"

#include <cstdio>

int main() {
    char buf[64];
    int64_t n = rust_greet_message("pcons", buf, sizeof(buf));
    if (n < 0) {
        std::fprintf(stderr, "rust_greet_message: buffer too small\n");
        return 1;
    }
    std::printf("%s\n", buf);
    std::printf("2 + 3 = %d (computed in Rust)\n", rust_greet_sum(2, 3));
    return 0;
}
