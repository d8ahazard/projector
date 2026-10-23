package com.projector.app.management

import org.junit.Test

class UpdatePolicyTest {
    private fun check(pkg: String = "com.projector.app", version: Long = 3, signers: Set<String> = setOf("trusted")) =
        UpdatePolicy.validate("com.projector.app", pkg, 2, version, 3, setOf("trusted"), signers)
    @Test fun acceptsSignedUpgrade() { check() }
    @Test(expected = IllegalArgumentException::class) fun rejectsDifferentPackage() { check(pkg="other.app") }
    @Test(expected = IllegalArgumentException::class) fun rejectsDifferentSigner() { check(signers=setOf("other")) }
    @Test(expected = IllegalArgumentException::class) fun rejectsRollback() { check(version=1) }
    @Test(expected = IllegalArgumentException::class) fun rejectsUnexpectedVersion() { check(version=4) }
}
