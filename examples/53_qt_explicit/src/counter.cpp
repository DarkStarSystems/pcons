// SPDX-License-Identifier: MIT
#include "counter.h"

void Counter::increment() {
    ++m_value;
    emit valueChanged(m_value);
}
