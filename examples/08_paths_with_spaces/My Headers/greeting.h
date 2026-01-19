/* SPDX-License-Identifier: MIT */
/* Header file in a directory with spaces in its name. */

#ifndef GREETING_H
#define GREETING_H

/* This gets overridden by the build system define */
#ifndef GREETING_MESSAGE
#define GREETING_MESSAGE "Default greeting"
#endif

void print_greeting(void);

#endif /* GREETING_H */
