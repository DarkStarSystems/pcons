// SPDX-License-Identifier: MIT
// A C++ type exposed to QML: QML_ELEMENT registers it under the module
// URI via qmltyperegistrar — no manual qmlRegisterType() calls.
#pragma once

#include <QObject>
#include <QtQml/qqmlregistration.h>

class Backend : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QString greeting READ greeting CONSTANT)
    Q_PROPERTY(int counter READ counter WRITE setCounter NOTIFY counterChanged)

public:
    explicit Backend(QObject *parent = nullptr) : QObject(parent) {}

    QString greeting() const { return QStringLiteral("Hello from C++"); }
    int counter() const { return m_counter; }
    void setCounter(int value);

signals:
    void counterChanged(int value);

private:
    int m_counter = 0;
};
