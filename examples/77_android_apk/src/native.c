#include <jni.h>

JNIEXPORT jint JNICALL
Java_org_pcons_apkexample_Native_answer(JNIEnv *env, jclass clazz)
{
    (void)env;
    (void)clazz;
    return 42;
}
