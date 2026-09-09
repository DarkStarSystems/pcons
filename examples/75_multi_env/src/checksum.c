/* SPDX-License-Identifier: MIT */
#include "checksum.h"

uint16_t checksum(const uint8_t *data, size_t len)
{
    uint16_t sum = 0;

    for (size_t i = 0; i < len; i++)
        sum = (uint16_t)(sum + data[i]);

    return sum;
}

const char *checksum_flavor(void)
{
#ifdef STRICT
    return "strict";
#else
    return "plain";
#endif
}
