[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Serial,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$Name,
    [Parameter(Mandatory)][ValidateSet('Snapshot','Power','Maintenance','Configure','Takeover','Debloat','Restore')][string]$Phase,
    [string]$Apk = '',
    [string]$ServerUrl = 'http://192.168.1.50:8000',
    [ValidateRange(1024,65535)][int]$AdbServerPort = 5037,
    [switch]$HomeVerified
)
$ErrorActionPreference = 'Stop'
$adb = 'D:\DevTools\Android\sdk\platform-tools\adb.exe'
$deviceDir = Join-Path 'D:\ProjectorAppData\devices' $Name
$snapshotPath = Join-Path $deviceDir 'original.json'
$homeSnapshotPath = Join-Path $deviceDir 'home-override-original.json'
$cecSnapshotPath = Join-Path $deviceDir 'cec-original.json'
$maintenanceSnapshotPath = Join-Path $deviceDir 'maintenance-original.json'
$supplementalSnapshotPath = Join-Path $deviceDir 'optional-debloat-original.json'
$applianceHome = 'com.projector.app/.ApplianceHome'
$apps = @('com.tubitv','com.google.android.youtube.tvunplugged','com.disney.disneyplus',
    'com.instagram.airwave','com.haystack.android','com.hulu.livingroomplus',
    'com.amazon.amazonvideo.livingroom','com.google.android.play.games','com.netflix.ninja',
    'com.netflix.tokenmanager','com.google.android.youtube.tv','com.google.android.youtube.tvmusic',
    'com.apple.atve.androidtv.appletv','com.espn.score_center','com.wbd.stream','com.cbs.ott',
    'com.google.android.apps.tv.launcherx','com.google.android.apps.tv.dreamx')
$settings = @(
    @('secure','screensaver_enabled','0'), @('secure','sleep_timeout','-1'),
    @('system','screen_off_timeout','2147483647'), @('global','stay_on_while_plugged_in','7')
)
function Invoke-Adb([string[]]$Arguments) {
    $output = & $adb -P $AdbServerPort -s $Serial @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ADB failed: $Arguments : $output" }
    return ($output -join "`n").Trim()
}
function Save-Json($Value, $Path) { $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8 }
$hardware = Invoke-Adb @('shell','getprop','ro.serialno')
if (!$hardware) { throw 'No device serial returned; refusing to provision.' }
New-Item -ItemType Directory -Path $deviceDir -Force | Out-Null
if ($Phase -eq 'Snapshot') {
    if (Test-Path -LiteralPath $snapshotPath) { throw 'Original snapshot already exists; preserve it and use Configure/Debloat/Restore.' }
    $inventory = Invoke-Adb @('shell','pm','list','packages')
    $inventory | Set-Content (Join-Path $deviceDir 'packages.txt')
    Invoke-Adb @('shell','dumpsys','package') | Set-Content (Join-Path $deviceDir 'package-state.txt')
    $states = @{}
    foreach ($pkg in $apps) {
        if (($inventory -split "`n") -contains "package:$pkg") {
            $details = Invoke-Adb @('shell','dumpsys','package',$pkg)
            if ($details -notmatch 'User 0:.*?installed=true.*?enabled=(\d+)') { throw "Cannot snapshot enabled state for $pkg" }
            $states[$pkg] = [int]$Matches[1]
        }
    }
    $savedSettings = @()
    foreach ($s in $settings) { $savedSettings += @{namespace=$s[0];key=$s[1];value=(Invoke-Adb @('shell','settings','get',$s[0],$s[1]))} }
    $oldHome = Invoke-Adb @('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.HOME')
    $oldHome = ($oldHome -split "`n" | Where-Object { $_ -match '^[\w.]+/[\w.]+' } | Select-Object -Last 1)
    if (!$oldHome) { throw 'Cannot identify original Home app.' }
    Save-Json @{hardware_serial=$hardware;endpoint=$Serial;name=$Name;captured_at=(Get-Date).ToString('o');home=$oldHome;packages=$states;settings=$savedSettings} $snapshotPath
    @"
# Restore the original launcher, package enabled states and changed power settings.
# The current wireless connection port may change after reboot; use adb mdns services.
& '$PSCommandPath' -Serial '$Serial' -Name '$Name' -Phase Restore -AdbServerPort $AdbServerPort
# Projector APK and its data are retained. No files are deleted.
"@ | Set-Content (Join-Path $deviceDir 'Restore.ps1')
    Write-Output "Snapshot and restore procedure saved: $snapshotPath"
    return
}
if (!(Test-Path -LiteralPath $snapshotPath)) { throw 'Run Snapshot first.' }
$snapshot = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
if ($snapshot.hardware_serial -ne $hardware) { throw 'Snapshot belongs to another device; refusing changes.' }
if ($Phase -eq 'Restore') {
    $commands = @{'0'='default-state';'1'='enable';'2'='disable';'3'='disable-user';'4'='disable-until-used'}
    foreach ($p in $snapshot.packages.PSObject.Properties) {
        Invoke-Adb @('shell','pm',$commands[[string]$p.Value],'--user','0',$p.Name) | Write-Output
    }
    if (Test-Path -LiteralPath $homeSnapshotPath) {
        $homeSnapshot = Get-Content -LiteralPath $homeSnapshotPath -Raw | ConvertFrom-Json
        foreach ($p in $homeSnapshot.PSObject.Properties) {
            Invoke-Adb @('shell','pm',$commands[[string]$p.Value],'--user','0',$p.Name) | Write-Output
        }
    }
    if (Test-Path -LiteralPath $supplementalSnapshotPath) {
        $supplemental = Get-Content -LiteralPath $supplementalSnapshotPath -Raw | ConvertFrom-Json
        foreach ($p in $supplemental.PSObject.Properties) {
            Invoke-Adb @('shell','pm',$commands[[string]$p.Value],'--user','0',$p.Name) | Write-Output
        }
    }
    Invoke-Adb @('shell','cmd','package','set-home-activity','--user','0',$snapshot.home) | Write-Output
    foreach ($s in $snapshot.settings) {
        if ($s.value -eq 'null') { Invoke-Adb @('shell','settings','delete',$s.namespace,$s.key) | Write-Output }
        else { Invoke-Adb @('shell','settings','put',$s.namespace,$s.key,$s.value) | Write-Output }
    }
    Invoke-Adb @('shell','am','broadcast','-f','0x20','-n','com.projector.app/.player.ProvisionReceiver','--ez','enabled','false') | Write-Output
    foreach ($op in @('SYSTEM_ALERT_WINDOW','REQUEST_INSTALL_PACKAGES')) {
        Invoke-Adb @('shell','appops','set','com.projector.app',$op,'default') | Write-Output
    }
    Invoke-Adb @('shell','pm','revoke','com.projector.app','android.permission.WRITE_SECURE_SETTINGS') | Write-Output
    if (Test-Path -LiteralPath $maintenanceSnapshotPath) {
        $saved = Get-Content -LiteralPath $maintenanceSnapshotPath -Raw | ConvertFrom-Json
        if ($saved.adb_allowed_connection_time -eq 'null') {
            Invoke-Adb @('shell','settings','delete','global','adb_allowed_connection_time') | Write-Output
        } else {
            Invoke-Adb @('shell','settings','put','global','adb_allowed_connection_time',[string]$saved.adb_allowed_connection_time) | Write-Output
        }
    }
    # Restore CEC last: enabling it can immediately put a non-selected HDMI source to sleep.
    if (Test-Path -LiteralPath $cecSnapshotPath) {
        $cec = Get-Content -LiteralPath $cecSnapshotPath -Raw | ConvertFrom-Json
        foreach ($key in @('power_state_change_on_active_source_lost','power_control_mode','hdmi_cec_enabled')) {
            Invoke-Adb @('shell','cmd','hdmi_control','cec_setting','set',$key,[string]$cec.$key) | Write-Output
        }
    }
    return
}
if ($Phase -eq 'Maintenance') {
    if (!(Test-Path -LiteralPath $maintenanceSnapshotPath)) {
        Save-Json @{adb_allowed_connection_time=(Invoke-Adb @('shell','settings','get','global','adb_allowed_connection_time'))} $maintenanceSnapshotPath
    }
    Invoke-Adb @('shell','pm','grant','com.projector.app','android.permission.WRITE_SECURE_SETTINGS') | Write-Output
    # Preserve paired-host authorization for this explicitly provisioned appliance.
    Invoke-Adb @('shell','settings','put','global','adb_allowed_connection_time','0') | Write-Output
    Invoke-Adb @('shell','am','broadcast','-f','0x20','-n','com.projector.app/.player.ProvisionReceiver',
        '--ez','enabled','true','--ez','maintenance_adb','true','--es','server_url',$ServerUrl) | Write-Output
    Invoke-Adb @('shell','am','start','-S','-n',$applianceHome) | Write-Output
    return
}
if ($Phase -in @('Power','Configure')) {
    $cecValues = [ordered]@{power_state_change_on_active_source_lost='none';power_control_mode='none';hdmi_cec_enabled='0'}
    if (!(Test-Path -LiteralPath $cecSnapshotPath)) {
        $cecOriginal = @{}
        foreach ($key in $cecValues.Keys) {
            $value = Invoke-Adb @('shell','cmd','hdmi_control','cec_setting','get',$key)
            if ($value -notmatch '=\s*(\S+)\s*$') { throw "Cannot read HDMI setting: $value" }
            $cecOriginal[$key] = $Matches[1]
        }
        Save-Json $cecOriginal $cecSnapshotPath
    }
    foreach ($key in $cecValues.Keys) {
        Invoke-Adb @('shell','cmd','hdmi_control','cec_setting','set',$key,$cecValues[$key]) | Write-Output
        $readback = Invoke-Adb @('shell','cmd','hdmi_control','cec_setting','get',$key)
        if ($readback -notmatch ('=\s*' + [regex]::Escape($cecValues[$key]) + '\s*$')) { throw "HDMI setting did not persist: $readback" }
    }
    if ($Phase -eq 'Power') { return }
}
if ($Phase -eq 'Takeover') {
    # This firmware prioritizes two system Home handlers above every installed app.
    # Only a completed setup with an enabled replacement Home is eligible.
    if ((Invoke-Adb @('shell','settings','get','secure','user_setup_complete')) -ne '1') { throw 'Finish Android setup first.' }
    $candidates = Invoke-Adb @('shell','cmd','package','query-activities','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.HOME')
    if ($candidates -notmatch 'com.projector.app/.ApplianceHome') { throw 'Enable Projector Home through Configure first.' }
    $homePackages = @('com.google.android.apps.tv.launcherx','com.google.android.tungsten.setupwraith')
    if ($candidates.Contains('com.google.android.tungsten.setupwraith/')) {
        # Disabling the setup wizard removes Wi-Fi configurations owned by its UID.
        # Fail BEFORE touching either launcher. Rejoin in Settings to transfer ownership.
        $wifi = Invoke-Adb @('shell','dumpsys','wifi')
        $owners = [regex]::Matches($wifi, '(?:cname|creatorName)=([^\s,]+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
        if (!$owners -or ($owners -match '^com\.google\.android\.tungsten\.setupwraith(?::|$)')) {
            throw 'Wi-Fi ownership is unverified or belongs to Google TV setup. Rejoin Wi-Fi through Android Settings, reconnect ADB, and retry. Neither Home package has been changed.'
        }
    }
    if (!(Test-Path -LiteralPath $homeSnapshotPath)) {
        $states = @{}
        foreach ($pkg in $homePackages) {
            if ($snapshot.packages.PSObject.Properties[$pkg]) { continue }
            if ($candidates.Contains("$pkg/")) {
                $details = Invoke-Adb @('shell','dumpsys','package',$pkg)
                if ($details -notmatch 'User 0:.*?installed=true.*?enabled=(\d+)') { throw "Cannot save $pkg state" }
                $states[$pkg] = [int]$Matches[1]
            }
        }
        Save-Json $states $homeSnapshotPath
    }
    try {
        foreach ($pkg in $homePackages) {
            if ($candidates.Contains("$pkg/")) { Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg) | Write-Output }
        }
        Invoke-Adb @('shell','cmd','package','set-home-activity','--user','0',$applianceHome) | Write-Output
        $resolved = Invoke-Adb @('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.HOME')
        if ($resolved -notmatch 'com.projector.app/') { throw 'Home still resolves elsewhere. Restore and investigate.' }
        Invoke-Adb @('shell','input','keyevent','KEYCODE_HOME') | Write-Output
    } catch {
        Write-Warning "Home switchover failed. Restore with: $PSCommandPath -Serial $Serial -Name $Name -Phase Restore"
        throw
    }
    return
}
if ($Phase -eq 'Configure') {
    if (!$Apk -or !(Test-Path -LiteralPath $Apk)) { throw 'Configure requires an existing signed -Apk.' }
    Invoke-Adb @('install','-r',$Apk) | Write-Output
    foreach ($op in @('SYSTEM_ALERT_WINDOW','REQUEST_INSTALL_PACKAGES')) {
        Invoke-Adb @('shell','appops','set','com.projector.app',$op,'allow') | Write-Output
    }
    Invoke-Adb @('shell','am','broadcast','-f','0x20','-n','com.projector.app/.player.ProvisionReceiver','--ez','enabled','true','--es','server_url',$ServerUrl) | Write-Output
    Invoke-Adb @('shell','cmd','package','set-home-activity','--user','0',$applianceHome) | Write-Output
    foreach ($s in $settings) { Invoke-Adb @('shell','settings','put',$s[0],$s[1],$s[2]) | Write-Output }
    Invoke-Adb @('shell','input','keyevent','KEYCODE_WAKEUP') | Write-Output
    Invoke-Adb @('shell','am','start','-n',$applianceHome) | Write-Output
    Write-Output 'Verify Home, reboot and LAN control before running Debloat -HomeVerified.'
    return
}
if (!$HomeVerified) { throw 'Verify replacement Home and boot first, then pass -HomeVerified.' }
$resolved = Invoke-Adb @('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.HOME')
if ($resolved -notmatch 'com.projector.app/') { throw 'Projector is not the default Home; refusing debloat.' }
$inventory = Invoke-Adb @('shell','pm','list','packages')
$supplemental = @{}
if (Test-Path -LiteralPath $supplementalSnapshotPath) {
    $savedSupplemental = Get-Content -LiteralPath $supplementalSnapshotPath -Raw | ConvertFrom-Json
    foreach ($p in $savedSupplemental.PSObject.Properties) { $supplemental[$p.Name] = [int]$p.Value }
}
foreach ($pkg in $apps) {
    if ($snapshot.packages.PSObject.Properties[$pkg] -or $supplemental.ContainsKey($pkg) -or
        !(($inventory -split "`n") -contains "package:$pkg")) { continue }
    $details = Invoke-Adb @('shell','dumpsys','package',$pkg)
    if ($details -notmatch 'User 0:.*?installed=true.*?enabled=(\d+)') { throw "Cannot snapshot enabled state for $pkg" }
    $supplemental[$pkg] = [int]$Matches[1]
}
if ($supplemental.Count) { Save-Json $supplemental $supplementalSnapshotPath }
$results = @()
foreach ($pkg in $apps) {
    if (!$snapshot.packages.PSObject.Properties[$pkg] -and !$supplemental.ContainsKey($pkg)) { continue }
    try {
        $result = Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg)
        if ($result -notmatch 'disabled-user') { throw $result }
        $results += @{package=$pkg;state='disabled-user';result=$result}
    } catch { $results += @{package=$pkg;state='failed';error=$_.Exception.Message} }
    Save-Json $results (Join-Path $deviceDir 'debloat-results.json')
}
$results | ForEach-Object { [pscustomobject]$_ } | Format-Table package,state
if ($results.state -contains 'failed') { throw 'Some packages could not be disabled; see debloat-results.json. No substitute removals attempted.' }
