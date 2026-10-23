plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.4.21" apply false
}

// Keep Android build outputs out of the source checkout.
allprojects {
    val outputRoot = System.getenv("PROJECTOR_ANDROID_BUILD_DIR") ?: if (System.getProperty("os.name").startsWith("Windows")) "D:/Builds/ProjectorApp/android" else null
    if (outputRoot != null) layout.buildDirectory.set(file(outputRoot + "/" + project.name))
}
