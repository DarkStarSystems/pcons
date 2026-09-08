/** SPDX-License-Identifier: MIT */
#include "metrics_limits.h"
#include <stdio.h>

int metrics_buckets(void);

int main(void) {
    printf("%s %d %d\n", METRICS_LABEL, METRICS_BUCKETS, metrics_buckets());
    return 0;
}
