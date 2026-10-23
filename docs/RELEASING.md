# Publishing releases

GitHub Actions runs backend tests, the production web build, and Android unit tests on every push and pull request.

Tags matching `v*` also build and publish a signed APK when these repository secrets are configured:

- `PROJECTOR_KEYSTORE_BASE64` — base64-encoded JKS file
- `PROJECTOR_KEYSTORE_PASSWORD`
- `PROJECTOR_KEY_ALIAS`
- `PROJECTOR_KEY_PASSWORD`

Create the base64 value without printing it into shell history where possible. Never commit the JKS. The workflow writes it only to the runner's temporary directory.

Release procedure:

1. Update release notes and run all tests locally.
2. Tag the commit, for example `v0.2.0`.
3. Push the tag.
4. Verify the GitHub Actions build and APK checksums.
5. Test the downloaded APK on one device before broad rollout.

GitHub's run number is added to a high monotonic version-code base. The visible Android version name comes from the tag.

Existing installations can update only from APKs signed with the same certificate. Public forks should use their own application ID or signing lineage if they intend to distribute independent builds.
