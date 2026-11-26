#include "pngish.h"

#include <stdio.h>

#include "zlite.h"

void pngish_describe(const char *who) {
    printf("pngish for %s, using zlite %s\n", who, zlite_version());
}
