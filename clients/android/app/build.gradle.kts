plugins {
    // AGP 9 has Kotlin support built in; the separate
    // org.jetbrains.kotlin.android plugin is an error now.
    id("com.android.application")
}

android {
    namespace = "com.tdg.loader"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.tdg.loader"
        // Android 11. The OS floor in the plan's device matrix: catches the
        // scoped-storage and permission differences that 12 through 17 papered over.
        minSdk = 30
        targetSdk = 37
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    // Deliberately thin. JSON comes from the platform's org.json and HTTP from
    // HttpURLConnection, so there is no OkHttp or Moshi to keep in step — the
    // same argument the rest of this repo makes for hand-rolling over depending.
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
}
