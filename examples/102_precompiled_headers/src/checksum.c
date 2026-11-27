#include "checksum.h"

#include <string.h>

unsigned checksum(const char *text) {
    unsigned sum = 0;
    for (size_t i = 0; i < strlen(text); ++i) {
        sum = sum * 31 + (unsigned char)text[i];
    }
    return sum;
}
