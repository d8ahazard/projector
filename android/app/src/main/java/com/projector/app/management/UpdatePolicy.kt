package com.projector.app.management

/** Independent of Android so rejection cases can be tested without a device. */
object UpdatePolicy {
    fun validate(expectedPackage: String, candidatePackage: String, installedVersion: Long,
                 candidateVersion: Long, expectedVersion: Long, installedSigners: Set<String>, candidateSigners: Set<String>) {
        require(candidatePackage == expectedPackage) { "Update is for a different application" }
        require(candidateVersion == expectedVersion && candidateVersion > installedVersion) { "Update version is stale or differs from the release" }
        require(installedSigners.isNotEmpty() && installedSigners == candidateSigners) { "Update signing certificate does not match this app" }
    }
}
