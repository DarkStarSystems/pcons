/* The DLL that ships as a private SxS assembly.

   It returns text rather than printing it, so the example's output order does
   not depend on how two modules happen to flush the same stream. */
#define MYLIB_BUILD
#include "mylib.h"

MYLIB_API const char *mylib_greeting(void) {
    return "Hello from MyLib!";
}

MYLIB_API int mylib_add(int a, int b) {
    return a + b;
}
