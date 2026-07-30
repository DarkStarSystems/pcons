// SPDX-License-Identifier: MIT
import QtQml
import PconsDemo

QtObject {
    id: root

    property Backend backend: Backend {
        onCounterChanged: (value) => {
            console.log("counter is now", value)
            if (value === 3)
                Qt.exit(0)
        }
    }

    Component.onCompleted: {
        console.log("QML says:", backend.greeting)
        backend.counter = 1
        backend.counter = 2
        backend.counter = 3
    }
}
