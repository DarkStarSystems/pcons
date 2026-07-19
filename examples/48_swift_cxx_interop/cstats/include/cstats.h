#ifndef CSTATS_H
#define CSTATS_H
/* Tiny C statistics library imported by Swift.
 *
 * The extern "C" guards matter: with Swift's C++ interop mode enabled,
 * the clang importer parses this header as C++, so without them the
 * Swift side would reference a C++-mangled symbol that the C-compiled
 * library doesn't export.
 */
#ifdef __cplusplus
extern "C" {
#endif

double cstats_mean2(double a, double b);

#ifdef __cplusplus
}
#endif
#endif /* CSTATS_H */
