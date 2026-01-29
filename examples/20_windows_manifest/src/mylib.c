/* Simple library for demonstrating assembly manifests. */
#include <stdio.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

EXPORT void mylib_hello(void) {
    printf("Hello from MyLib!\n");
}

EXPORT int mylib_add(int a, int b) {
    return a + b;
}
