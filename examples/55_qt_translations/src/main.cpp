// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QTextStream>
#include <QTranslator>

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    QTextStream out(stdout);

    // Untranslated first...
    out << QCoreApplication::translate("main", "Hello, world!") << "\n";

    // ...then load the embedded German catalog from resources.
    QTranslator translator;
    if (!translator.load(QStringLiteral("app_de"), QStringLiteral(":/i18n"))) {
        out << "failed to load translation!\n";
        return 1;
    }
    QCoreApplication::installTranslator(&translator);
    out << QCoreApplication::translate("main", "Hello, world!") << "\n";
    return 0;
}
