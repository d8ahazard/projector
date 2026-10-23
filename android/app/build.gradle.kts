plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystorePath: String? = System.getenv("PROJECTOR_KEYSTORE")

android {
    namespace = "com.projector.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.projector.app"
        minSdk = 26
        targetSdk = 34
        versionCode = (System.getenv("PROJECTOR_VERSION_CODE") ?: "4").toInt()
        versionName = System.getenv("PROJECTOR_VERSION_NAME") ?: "0.2.0"
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("PROJECTOR_KEYSTORE_PASSWORD") ?: "projector"
                keyAlias = System.getenv("PROJECTOR_KEY_ALIAS") ?: "projector"
                keyPassword = System.getenv("PROJECTOR_KEY_PASSWORD") ?: "projector"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePath != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")

    implementation("androidx.media3:media3-exoplayer:1.4.1")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    val camerax = "1.3.4"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")

    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    testImplementation("junit:junit:4.13.2")
}
