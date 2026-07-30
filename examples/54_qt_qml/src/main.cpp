// SPDX-License-Identifier: MIT
#include <QCoreApplication>
#include <QQmlApplicationEngine>

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
    // :/qt/qml is on the engine's default import path; the module's
    // qmldir, QML files, and type registrations are all compiled in.
    engine.loadFromModule("PconsDemo", "Main");
    if (engine.rootObjects().isEmpty())
        return 1;
    return app.exec();
}
