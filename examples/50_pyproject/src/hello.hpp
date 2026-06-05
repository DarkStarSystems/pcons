#pragma once

#include <string_view>

#if defined(_WIN32)
#  if defined(HELLO_LIB_EXPORTS)
#    define HELLO_API __declspec(dllexport)
#  else
#    define HELLO_API __declspec(dllimport)
#  endif
#else
#  define HELLO_API
#endif

HELLO_API std::string_view hello();
