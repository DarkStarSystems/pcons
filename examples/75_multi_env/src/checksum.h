/* SPDX-License-Identifier: MIT */
#pragma once

#include <stddef.h>
#include <stdint.h>

uint16_t checksum(const uint8_t *data, size_t len);
const char *checksum_flavor(void);
