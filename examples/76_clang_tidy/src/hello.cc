// SPDX-License-Identifier: MIT
#include <cstdio>

int main() {
    // 42 is a magic number: readability-magic-numbers reports it, and the
    // build goes on, since a clang-tidy warning is not an error.
    int answer = 42;
    std::printf("Analyzed by clang-tidy, answer %d\n", answer);
    return 0;
}
