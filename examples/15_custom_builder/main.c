/* Example program using the generated version header */
#include <stdio.h>
#include "version.h"

int main(void) {
    printf("%s version %s\n", APP_NAME, VERSION_STRING);
    printf("Version components: %d.%d.%d\n",
           VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH);
    return 0;
}
