// SPDX-License-Identifier: MIT
#include <cstdio>

extern "C" int gpu_add_one(int v, const char** error);

int main() {
    std::puts("CUDA runtime linked");
    const char* error = "";
    int result = gpu_add_one(41, &error);
    if (result < 0)
        std::printf("No GPU to run on: %s\n", error);
    else
        std::printf("The GPU says %d\n", result);
    return 0;
}
