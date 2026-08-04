// SPDX-License-Identifier: MIT
// Tiny code-generator stand-in: concatenates its input files into one
// output file, after a two-line header.
//
// Every argument is a flag, so a mis-expanded command line is an error
// rather than something that quietly produces the wrong file:
//
//   bundler --out=PATH --stamp=TEXT -iINPUT...

#include <stdio.h>
#include <string.h>

static int copy_file(const char *path, FILE *out) {
    char buf[4096];
    size_t n;
    FILE *in = fopen(path, "rb");
    if (in == NULL) {
        fprintf(stderr, "bundler: cannot open %s\n", path);
        return 1;
    }
    while ((n = fread(buf, 1, sizeof buf, in)) > 0) {
        fwrite(buf, 1, n, out);
    }
    fclose(in);
    return 0;
}

int main(int argc, char **argv) {
    const char *out_path = NULL;
    const char *stamp = "(none)";
    FILE *out;
    int i;
    int inputs = 0;
    int rc = 0;

    for (i = 1; i < argc; ++i) {
        if (strncmp(argv[i], "--out=", 6) == 0) {
            out_path = argv[i] + 6;
        } else if (strncmp(argv[i], "--stamp=", 8) == 0) {
            stamp = argv[i] + 8;
        } else if (strncmp(argv[i], "-i", 2) == 0) {
            ++inputs;
        } else {
            fprintf(stderr, "bundler: unexpected argument '%s'\n", argv[i]);
            return 2;
        }
    }

    if (out_path == NULL) {
        fprintf(stderr, "bundler: --out=PATH is required\n");
        return 2;
    }

    out = fopen(out_path, "wb");
    if (out == NULL) {
        fprintf(stderr, "bundler: cannot write %s\n", out_path);
        return 1;
    }

    fprintf(out, "stamp: %s\n", stamp);
    fprintf(out, "inputs: %d\n", inputs);

    for (i = 1; i < argc; ++i) {
        if (strncmp(argv[i], "-i", 2) == 0) {
            rc |= copy_file(argv[i] + 2, out);
        }
    }

    fclose(out);
    return rc;
}
