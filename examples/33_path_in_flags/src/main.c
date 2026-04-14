// SPDX-License-Identifier: MIT
#include <stdio.h>

extern void mylib_hello(void);

int main(void) {
    printf("Main program\n");
    mylib_hello();
    return 0;
}
