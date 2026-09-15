// SPDX-License-Identifier: MIT
#pragma once

#include <QObject>

// Q_OBJECT means moc has to run on this header. automoc finds it by
// scanning the target's sources, wherever the script declaring them lives.
class Counter : public QObject {
    Q_OBJECT

public:
    explicit Counter(QObject *parent = nullptr) : QObject(parent) {}

    int value() const { return m_value; }

signals:
    void bumped(int value);

public slots:
    void bump() {
        m_value += 1;
        emit bumped(m_value);
    }

private:
    int m_value = 0;
};
