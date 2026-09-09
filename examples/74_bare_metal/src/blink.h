/* SPDX-License-Identifier: MIT */
#ifndef BLINK_H
#define BLINK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint32_t blink_next(uint32_t tick);
const char *firmware_banner(void);

#ifdef __cplusplus
}
#endif

#endif
