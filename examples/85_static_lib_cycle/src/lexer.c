// SPDX-License-Identifier: MIT
#include "lexer.h"

#include "parser.h"

int lexer_count(const char *s) {
    int count = 0;
    int in_word = 0;
    for (; *s; s++) {
        if (parser_is_delim(*s)) {
            in_word = 0;
        } else if (!in_word) {
            in_word = 1;
            count++;
        }
    }
    return count;
}
