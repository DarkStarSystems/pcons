// SPDX-License-Identifier: MIT
import QtQml
import PconsNested

QtObject {
    property Detail detail: Detail {}
    property Badge badge: Badge {}

    Component.onCompleted: {
        console.log("type Detail is", detail.label)
        console.log("type Badge is", badge.label)
    }
}
