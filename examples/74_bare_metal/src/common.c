/* SPDX-License-Identifier: MIT */
#include "common.h"

uint16_t frame_checksum(const uint8_t *data, uint32_t len)
{
    uint16_t sum = 0xffffu;

    for (uint32_t i = 0; i < len; i++) {
        sum ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; bit++)
            sum = (sum & 0x8000u) ? (uint16_t)((sum << 1) ^ 0x1021u)
                                  : (uint16_t)(sum << 1);
    }

    return sum;
}
