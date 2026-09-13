// SPDX-License-Identifier: MIT
package org.pcons.qtapkexample;

import android.os.Build;

public class AppInfo {
    public static String describe() {
        return "Java says: Android API " + Build.VERSION.SDK_INT + " on " + Build.MODEL;
    }
}
