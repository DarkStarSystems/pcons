// SPDX-License-Identifier: MIT
#pragma once

#include <QMainWindow>

QT_BEGIN_NAMESPACE
namespace Ui {
class MainWindow;
}
QT_END_NAMESPACE

class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

    // Exercises the Designer form, the resource file, and a queued
    // signal/slot round trip; prints what it finds. Returns true when
    // everything the build generated is present and wired.
    bool selfCheck();

signals:
    void checked(const QString &message);

private slots:
    void onChecked(const QString &message);

private:
    Ui::MainWindow *ui;
    QString m_lastMessage;
};
