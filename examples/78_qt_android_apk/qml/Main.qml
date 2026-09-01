// SPDX-License-Identifier: MIT
import QtQuick

Window {
    width: 360
    height: 240
    visible: true
    title: "pcons Qt Android example"

    Rectangle {
        anchors.fill: parent
        color: "#101418"

        Text {
            anchors.centerIn: parent
            color: "#e6edf3"
            font.pixelSize: 18
            horizontalAlignment: Text.AlignHCenter
            text: javaGreeting
        }
    }
}
