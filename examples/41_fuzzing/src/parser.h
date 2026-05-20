/* SPDX-License-Identifier: MIT */
#ifndef PARSER_H
#define PARSER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Look for a well-formed "key=value" pair in a byte buffer.
 *
 * Returns 1 if the buffer is exactly one key=value where the key is
 * non-empty and consists of [A-Za-z0-9_] and the value is non-empty;
 * 0 otherwise.
 *
 * This is the "code under test." It must not read past `size`, write
 * memory, or allocate — exactly the kind of property a fuzzer is good
 * at stressing.
 */
int parse_keyvalue(const uint8_t *data, size_t size);

#ifdef __cplusplus
}
#endif

#endif
