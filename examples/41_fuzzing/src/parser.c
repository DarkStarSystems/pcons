/* SPDX-License-Identifier: MIT */
#include "parser.h"

static int is_key_char(unsigned char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
        || (c >= '0' && c <= '9') || c == '_';
}

int parse_keyvalue(const uint8_t *data, size_t size) {
    if (size < 3) return 0;  /* need at least "k=v" */
    size_t eq = 0;
    int found = 0;
    for (size_t i = 0; i < size; ++i) {
        if (data[i] == '=') { eq = i; found = 1; break; }
    }
    if (!found) return 0;
    if (eq == 0 || eq == size - 1) return 0;
    for (size_t i = 0; i < eq; ++i) {
        if (!is_key_char(data[i])) return 0;
    }
    return 1;
}
