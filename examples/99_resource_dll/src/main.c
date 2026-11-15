// SPDX-License-Identifier: MIT
// Load the strings DLL as data and print its greeting.
#include <stdio.h>
#include <windows.h>

#define IDS_GREETING 1

int main(void) {
    HMODULE strings = LoadLibraryExA(
        "strings_fr.dll", NULL,
        LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE);
    if (strings == NULL) {
        fprintf(stderr, "can't load strings_fr.dll (error %lu)\n", GetLastError());
        return 1;
    }
    char greeting[128];
    if (LoadStringA(strings, IDS_GREETING, greeting, sizeof greeting) == 0) {
        fprintf(stderr, "no greeting in strings_fr.dll\n");
        return 1;
    }
    printf("%s\n", greeting);
    FreeLibrary(strings);
    return 0;
}
