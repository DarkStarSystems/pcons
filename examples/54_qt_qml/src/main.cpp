// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QQmlApplicationEngine>
#include <QUrl>

#include <cstdio>

int main(int argc, char *argv[]) {
    // QML console.log goes to stderr by default; send it to stdout so
    // this example's output is easy to check.
    qInstallMessageHandler(
        [](QtMsgType, const QMessageLogContext &, const QString &msg) {
            std::printf("%s\n", qPrintable(msg));
            std::fflush(stdout);
        });

    QCoreApplication app(argc, argv);

    QQmlApplicationEngine engine;
    // The module's qmldir, QML files, and type registrations are all
    // compiled in under :/qt/qml — the engine's default import path
    // since Qt 6.5; add it explicitly for older releases.
    engine.addImportPath(QStringLiteral(":/qt/qml"));
#if QT_VERSION >= QT_VERSION_CHECK(6, 5, 0)
    engine.loadFromModule("PconsDemo", "Main");
#else
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/PconsDemo/Main.qml")));
#endif
    if (engine.rootObjects().isEmpty())
        return 1;
    return app.exec();
}
