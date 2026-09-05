// SPDX-License-Identifier: MIT
import QtQml

QtObject {
    property Badge badge: Badge {}
    property string label: "qml/pages/Detail.qml"
    property string neighbor: badge.label
}
