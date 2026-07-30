// SPDX-License-Identifier: MIT
#include <QApplication>
#include <QLabel>

int main(int argc, char *argv[]) {
    qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    QApplication app(argc, argv);
    QLabel label(QStringLiteral("Deployable Qt app"));
    label.show();
    return 0;
}
