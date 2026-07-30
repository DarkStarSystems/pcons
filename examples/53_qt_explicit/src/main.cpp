// SPDX-License-Identifier: MIT
// Demonstrates the two moc modes and rcc:
//  - counter.h (Q_OBJECT in a header) -> moc_counter.cpp, compiled separately
//  - this file (Q_OBJECT in a .cpp)   -> main.moc, #included below
//  - messages.qrc                     -> qrc_messages.cpp (Q_INIT_RESOURCE)
#include <QCoreApplication>
#include <QFile>
#include <QObject>
#include <QTextStream>

#include "counter.h"

// A local QObject defined in a source file: moc output must be
// #included at the end of this file (see last line).
class Watcher : public QObject {
    Q_OBJECT
public slots:
    void onValueChanged(int v) { last = v; }

public:
    int last = -1;
};

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    Q_INIT_RESOURCE(messages);

    QTextStream out(stdout);

    QFile greeting(":/greeting.txt");
    if (greeting.open(QIODevice::ReadOnly | QIODevice::Text)) {
        out << QString::fromUtf8(greeting.readAll()).trimmed() << "\n";
    }

    Counter counter;
    Watcher watcher;
    QObject::connect(&counter, &Counter::valueChanged, &watcher,
                     &Watcher::onValueChanged);
    counter.increment();
    counter.increment();
    counter.increment();

    out << "counter reached " << watcher.last << "\n";
    return watcher.last == 3 ? 0 : 1;
}

#include "main.moc"
