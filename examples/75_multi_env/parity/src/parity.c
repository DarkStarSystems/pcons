/* SPDX-License-Identifier: MIT */
#include "parity.h"

int parity(uint16_t value)
{
    int bits = 0;

    while (value) {
        bits += value & 1u;
        value >>= 1u;
    }

    return bits & 1;
}

const char *parity_flavor(void)
{
#ifdef STRICT
    return "strict";
#else
    return "plain";
#endif
}
