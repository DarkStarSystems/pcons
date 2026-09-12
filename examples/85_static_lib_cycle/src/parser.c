// SPDX-License-Identifier: MIT
#include "parser.h"

#include "lexer.h"

int parser_tokens(const char *s) { return lexer_count(s); }
