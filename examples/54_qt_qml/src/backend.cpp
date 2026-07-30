// SPDX-License-Identifier: MIT
#include "backend.h"

void Backend::setCounter(int value) {
    if (value != m_counter) {
        m_counter = value;
        emit counterChanged(value);
    }
}
