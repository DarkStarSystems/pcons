// SPDX-License-Identifier: MIT
#include <QApplication>

#include "mainwindow.h"

int main(int argc, char *argv[]) {
    // Run headless (CI-friendly); remove for a real windowed app.
    qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));

    QApplication app(argc, argv);
    MainWindow window;
    window.show();
    return window.selfCheck() ? 0 : 1;
}
