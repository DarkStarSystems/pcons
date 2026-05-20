/* SPDX-License-Identifier: MIT */
/*
 * libFuzzer entrypoint.
 *
 * libFuzzer supplies its own main() when you link with
 * `-fsanitize=fuzzer`, so the only thing this file needs is the input
 * callback. The fuzzer drives it with mutated bytes; AddressSanitizer
 * (also enabled via the build flags) reports any memory issue as a
 * crash, which libFuzzer captures into a `crash-*` artifact.
 *
 * Keep the callback fast — libFuzzer will call it millions of times.
 *
 * The file is .cpp so pcons links with clang++. libFuzzer is itself a
 * C++ library and needs the C++ runtime; pulling that in via the C++
 * driver is more reliable than adding -lc++/-lstdc++ explicitly.
 */
#include "parser.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    (void)parse_keyvalue(data, size);
    return 0;
}
