/* SPDX-License-Identifier: MIT */
/*
 * Source file demonstrating paths with spaces.
 *
 * This file is in "src with spaces/my program.c" and includes
 * a header from "My Headers/greeting.h". The build system must
 * properly quote these paths for the compiler.
 */

#include <stdio.h>
#include "greeting.h"

void print_greeting(void) {
    printf("%s\n", GREETING_MESSAGE);
}

int main(void) {
    printf("Program built from path with spaces!\n");
    print_greeting();
    return 0;
}
