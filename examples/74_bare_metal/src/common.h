/* SPDX-License-Identifier: MIT */
#ifndef COMMON_H
#define COMMON_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Frame checksum, shared by the firmware and the host-side test that
   proves the algorithm before it is flashed. Freestanding: no libc. */
uint16_t frame_checksum(const uint8_t *data, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif
