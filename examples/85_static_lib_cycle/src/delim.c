// SPDX-License-Identifier: MIT
#include "parser.h"

int parser_is_delim(char c) { return c == ' ' || c == ',' || c == '\n'; }
