/* SPDX-License-Identifier: MIT */
/* Reads a plugin definition file and writes the plugin list, one name per
 * line.  Stands in for a real project's definition-language compiler: the
 * build has to build and run this program before it knows what to build. */

#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <plugins.def> <plugins-list.txt>\n", argv[0]);
        return 2;
    }

    FILE *in = fopen(argv[1], "r");
    if (!in) {
        perror(argv[1]);
        return 1;
    }
    FILE *out = fopen(argv[2], "w");
    if (!out) {
        perror(argv[2]);
        fclose(in);
        return 1;
    }

    char line[256];
    while (fgets(line, sizeof line, in)) {
        char *name = line;
        while (*name == ' ' || *name == '\t') name++;
        name[strcspn(name, "\r\n")] = '\0';
        if (*name == '\0' || *name == '#') continue;
        fprintf(out, "%s\n", name);
    }

    fclose(out);
    fclose(in);
    return 0;
}
