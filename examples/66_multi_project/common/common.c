/* SPDX-License-Identifier: MIT */
#include "common.h"

unsigned checksum(const char *s) {
    unsigned sum = 0;
    while (*s)
        sum = sum * 31 + (unsigned char)*s++;
    return sum;
}
