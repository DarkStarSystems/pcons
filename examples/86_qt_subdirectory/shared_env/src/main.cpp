// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QString>
#include <QTextStream>

#include "greeter.h"

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);

    Greeter greeter;
    QString heard;
    QObject::connect(&greeter, &Greeter::greeted,
                     [&heard](const QString &message) { heard = message; });
    greeter.greet();

    QTextStream out(stdout);
    out << "shared_env: " << greeter.metaObject()->className() << " " << heard << "\n";

    return heard.isEmpty() ? 1 : 0;
}
