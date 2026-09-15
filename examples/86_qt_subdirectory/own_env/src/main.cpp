// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QTextStream>

#include "counter.h"

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);

    Counter counter;
    int seen = -1;
    QObject::connect(&counter, &Counter::bumped, [&seen](int value) { seen = value; });
    counter.bump();

    QTextStream out(stdout);
    // The class name comes from the meta-object, so it is there only if
    // moc_counter.cpp was generated and compiled.
    out << "own_env: " << counter.metaObject()->className() << " signal=" << seen
        << "\n";

    return seen == counter.value() ? 0 : 1;
}
