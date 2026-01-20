#include <stdio.h>

#ifdef HAS_EXTRA_FEATURE
#include "extra.h"
#endif

void extra_function(void) {
#ifdef HAS_EXTRA_FEATURE
    printf("Extra feature: %s\n", EXTRA_MESSAGE);
#else
    printf("No extra feature\n");
#endif
}
