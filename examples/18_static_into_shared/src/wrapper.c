/* SPDX-License-Identifier: MIT */
/* Wrapper that uses core - compiled into a shared library. */

#include "core.h"  /* Should be found via public include_dirs from core_lib */

/* On Windows, functions must be explicitly exported from a DLL */
#ifdef _WIN32
#define WRAPPER_API __declspec(dllexport)
#else
#define WRAPPER_API
#endif

WRAPPER_API int wrapper_get_value(void)
{
    /* Call into the static library */
    return core_value() * 2;
}
