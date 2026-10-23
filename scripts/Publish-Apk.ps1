[CmdletBinding()]
param([string]$Apk = 'D:\Builds\ProjectorApp\android\app\outputs\apk\release\app-release.apk',
      [string]$BuildTools = 'D:\DevTools\Android\sdk\build-tools\34.0.0')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $projectRoot 'data\apk\projector.apk'
$metadata = Join-Path $projectRoot 'data\apk\projector-release.json'
$aapt = Join-Path $BuildTools 'aapt.exe'
$signer = Join-Path $BuildTools 'apksigner.bat'
function Get-Certificate([string]$File) {
    $result = & $signer verify --print-certs $File
    if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed: $File" }
    $line = $result | Where-Object { $_ -match '^Signer #1 certificate SHA-256 digest:' }
    if (!$line) { throw 'APK signer certificate missing' }
    return $line.Trim()
}
$cert = Get-Certificate $Apk
if ((Test-Path $destination) -and (Get-Certificate $destination) -ne $cert) { throw 'Signing key differs from the installed release; refusing publication' }
$details = & $aapt dump badging $Apk
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect APK' }
$packageLine = ($details | Where-Object { $_ -match '^package:' })
if ($packageLine -notmatch "\bname='com\.projector\.app'\s+versionCode='(\d+)'\s+versionName='([^']+)'" ) { throw 'Wrong APK package or missing version metadata' }
$versionCode = [long]$Matches[1]; $versionName = $Matches[2]
$sha = (Get-FileHash -LiteralPath $Apk -Algorithm SHA256).Hash.ToLowerInvariant()
if (Test-Path $destination) {
    $old = & $aapt dump badging $destination
    $oldLine = $old | Where-Object { $_ -match '^package:' }
    if ($oldLine -match "versionCode='(\d+)'" -and $versionCode -lt [long]$Matches[1]) { throw 'Refusing an APK downgrade' }
    $oldHash = (Get-FileHash $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($oldLine -match "versionCode='(\d+)'" -and $versionCode -eq [long]$Matches[1] -and $oldHash -ne $sha) { throw 'Different APK needs a higher versionCode' }
    $backup = 'D:\ProjectorAppData\backups\apk'
    New-Item -ItemType Directory -Force $backup | Out-Null
    if (!(Test-Path (Join-Path $backup "$oldHash.apk"))) { Copy-Item -LiteralPath $destination -Destination (Join-Path $backup "$oldHash.apk") }
}
$release = @{package='com.projector.app';version_code=$versionCode;version_name=$versionName;sha256=$sha;size=(Get-Item $Apk).Length;published_at=[DateTime]::UtcNow.ToString('o')}
Copy-Item -LiteralPath $Apk -Destination ($destination+'.pending')
Move-Item -LiteralPath ($destination+'.pending') -Destination $destination -Force
$release | ConvertTo-Json | Set-Content -LiteralPath ($metadata+'.pending') -Encoding UTF8
Move-Item -LiteralPath ($metadata+'.pending') -Destination $metadata -Force
$release | ConvertTo-Json
