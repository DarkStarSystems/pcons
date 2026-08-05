/* Interface of the DLL that ships as a private SxS assembly. */
#ifndef MYLIB_H
#define MYLIB_H

#if defined(_WIN32) && !defined(MYLIB_BUILD)
#define MYLIB_API __declspec(dllimport)
#elif defined(_WIN32)
#define MYLIB_API __declspec(dllexport)
#else
#define MYLIB_API
#endif

MYLIB_API const char *mylib_greeting(void);
MYLIB_API int mylib_add(int a, int b);

#endif
