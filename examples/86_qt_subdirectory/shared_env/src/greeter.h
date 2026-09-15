// SPDX-License-Identifier: MIT
#pragma once

#include <QObject>
#include <QString>

class Greeter : public QObject {
    Q_OBJECT

public:
    explicit Greeter(QObject *parent = nullptr) : QObject(parent) {}

signals:
    void greeted(const QString &message);

public slots:
    void greet() { emit greeted(QStringLiteral("hello from shared_env")); }
};
