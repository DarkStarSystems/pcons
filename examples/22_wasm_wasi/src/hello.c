/* SPDX-License-Identifier: MIT */
/* Simple WASI program — prints a greeting and exits. */
#include <stdio.h>

int main(void) {
    printf("Hello from WebAssembly (WASI)!\n");
    return 0;
}
