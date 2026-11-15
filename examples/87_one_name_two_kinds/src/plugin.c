// SPDX-License-Identifier: MIT
#include "kind.h"

/* What an .ofx host would call, so the library exports it. */
#ifdef _WIN32
__declspec(dllexport)
#endif
int plugin_kind(void)
{
    return kind();
}
