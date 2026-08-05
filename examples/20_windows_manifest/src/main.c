/* Simple Windows application demonstrating manifest usage. */
#include <stdio.h>

#include "mylib.h"

#ifdef _WIN32
#include <windows.h>
#endif

int main(void) {
#ifdef _WIN32
    /* Check if the application is DPI aware */
    UINT dpi = 96;  /* Default DPI */

    /* Try to get the DPI using the newer API */
    HMODULE user32 = GetModuleHandleW(L"user32.dll");
    if (user32) {
        typedef UINT (WINAPI *GetDpiForSystemFunc)(void);
        GetDpiForSystemFunc pGetDpiForSystem =
            (GetDpiForSystemFunc)GetProcAddress(user32, "GetDpiForSystem");
        if (pGetDpiForSystem) {
            dpi = pGetDpiForSystem();
        }
    }

    printf("Hello from Windows manifest example!\n");
    printf("System DPI: %u\n", dpi);

    /* MyLib.dll is not beside this executable; it is found only through the
       activation context built from the manifests. Reaching this line at all
       means SxS resolution worked. */
    printf("%s\n", mylib_greeting());
    printf("2 + 3 = %d\n", mylib_add(2, 3));

    if (dpi > 96) {
        printf("Running on a high-DPI display\n");
    }
#else
    printf("Hello from Windows manifest example!\n");
    printf("(DPI awareness features only work on Windows)\n");
    printf("%s\n", mylib_greeting());
    printf("2 + 3 = %d\n", mylib_add(2, 3));
#endif

    return 0;
}
