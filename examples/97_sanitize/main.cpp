// SPDX-License-Identifier: MIT
// Sums an array; given an argument, reads one element past its end, which
// AddressSanitizer reports.
#include <cstdio>
#include <vector>

int main(int argc, char** argv) {
    std::vector<int>* values = new std::vector<int>{1, 2, 3};
    volatile std::size_t count = values->size() + (argc > 1 ? 1 : 0);
    int sum = 0;
    for (std::size_t i = 0; i < count; ++i) {
        sum += values->data()[i];
    }
    std::printf("sum %d\n", sum);
    delete values;
    return 0;
}
