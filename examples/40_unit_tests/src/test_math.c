/* SPDX-License-Identifier: MIT */
/*
 * Tiny "test runner" for the math library.
 *
 * Each subcommand exercises one case and exits with the conventional
 * "0 = pass, non-zero = fail" the pcons test runner expects. A real
 * project would use Unity, CTest, Catch2, etc. — but for an example
 * the goal is to keep dependencies at zero.
 */

#include <stdio.h>
#include <string.h>
#include "math.h"

static int test_add(void) {
    if (math_add(2, 3) != 5) {
        fprintf(stderr, "add(2,3) != 5\n");
        return 1;
    }
    return 0;
}

static int test_mul(void) {
    if (math_mul(4, 5) != 20) {
        fprintf(stderr, "mul(4,5) != 20\n");
        return 1;
    }
    return 0;
}

static int test_always_fails(void) {
    fprintf(stderr, "this test always fails (used to demonstrate should_fail)\n");
    return 1;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {add|mul|fail}\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "add") == 0) return test_add();
    if (strcmp(argv[1], "mul") == 0) return test_mul();
    if (strcmp(argv[1], "fail") == 0) return test_always_fails();
    fprintf(stderr, "unknown test: %s\n", argv[1]);
    return 2;
}
