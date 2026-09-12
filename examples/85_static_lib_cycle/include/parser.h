// SPDX-License-Identifier: MIT
#ifndef PARSER_H
#define PARSER_H

/* Whether c separates two words. */
int parser_is_delim(char c);

/* Number of words in s. */
int parser_tokens(const char *s);

#endif
