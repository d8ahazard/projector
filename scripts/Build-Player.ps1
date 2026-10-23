[CmdletBinding()]
param([int]$VersionCode = 0)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $projectRoot 'data\apk\projector-release.json'
if ($VersionCode -eq 0) {
    if (!(Test-Path $manifest)) { throw 'Specify -VersionCode for the first release.' }
    $VersionCode = [int](Get-Content $manifest -Raw | ConvertFrom-Json).version_code + 1
}
$env:ANDROID_HOME = 'D:\DevTools\Android\sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:GRADLE_USER_HOME = 'D:\DevCache\Gradle'
$env:PROJECTOR_ANDROID_BUILD_DIR = 'D:\Builds\ProjectorApp\android'
$env:PROJECTOR_KEYSTORE = Join-Path $projectRoot 'data\keystore\release.jks'
$env:PROJECTOR_VERSION_CODE = "$VersionCode"
$env:TEMP = 'D:\ProjectorAppData\temp'
$env:TMP = $env:TEMP
Push-Location (Join-Path $projectRoot 'android')
try {
    & D:\DevTools\Android\gradle-8.9\bin\gradle.bat --no-daemon --project-cache-dir D:\DevCache\Gradle\projector testDebugUnitTest assembleRelease
    if ($LASTEXITCODE -ne 0) { throw 'Android tests or release build failed; existing release preserved.' }
    & (Join-Path $PSScriptRoot 'Publish-Apk.ps1')
} finally { Pop-Location }
