// SPDX-License-Identifier: MIT
#include <stdio.h>

#include "parser.h"

int main(void) {
    printf("%d tokens\n", parser_tokens("alpha beta, gamma"));
    return 0;
}
