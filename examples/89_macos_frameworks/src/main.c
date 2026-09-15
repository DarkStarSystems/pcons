/* SPDX-License-Identifier: MIT */
#include <stdio.h>

#include "fwlib.h"

/* This program never mentions CoreFoundation: fwlib's public.frameworks
 * usage requirement carries it onto app's link line. */
int main(void) {
    printf("string length: %ld\n", fwlib_string_length());
    return 0;
}
