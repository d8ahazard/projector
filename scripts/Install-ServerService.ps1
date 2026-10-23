[CmdletBinding()]
param(
    [switch]$Elevated,
    [int]$Port = 8000,
    [string]$OperatorSid = '',
    [string]$WorkDir = 'D:\ProjectorAppData',
    [string]$SourceRoot = 'D:\ProjectionPacks'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$serviceRoot = Join-Path $WorkDir 'service'
$wrapper = Join-Path $serviceRoot 'ProjectorServer.exe'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (!$OperatorSid) { $OperatorSid = $identity.User.Value }
if ($OperatorSid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'Invalid service operator SID' }
if (!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator rights were not granted.' }
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -Port $Port -OperatorSid $OperatorSid -WorkDir `"$WorkDir`" -SourceRoot `"$SourceRoot`""
    $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments -PassThru
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Service installation failed. Read $serviceRoot\install.log" }
    return
}
New-Item -ItemType Directory -Force $serviceRoot,(Join-Path $WorkDir 'logs'),(Join-Path $WorkDir 'temp'),$SourceRoot | Out-Null
Start-Transcript -Path (Join-Path $serviceRoot 'install.log') -Append
try {
    # Official, pinned WinSW release. Never replace a running wrapper silently.
    if (!(Test-Path -LiteralPath $wrapper)) {
        Invoke-WebRequest 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' -OutFile $wrapper -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash -ne '05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA') {
        throw 'WinSW binary does not match the pinned release hash.'
    }
    $python = Join-Path $projectRoot 'server\.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { throw "Python environment missing: $python" }
    $existing = Get-Service ProjectorServer -ErrorAction SilentlyContinue
    if ($existing -and $existing.Status -ne 'Stopped') { Stop-Service ProjectorServer; $existing.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60)) }
    $e = [Security.SecurityElement]::Escape($projectRoot)
    $workEsc = [Security.SecurityElement]::Escape($WorkDir)
    $sourceEsc = [Security.SecurityElement]::Escape($SourceRoot)
    @"
<service>
  <id>ProjectorServer</id>
  <name>Projector Server</name>
  <description>Local projection library, player control and synchronized show server.</description>
  <executable>$e\server\.venv\Scripts\python.exe</executable>
  <arguments>-m uvicorn app.main:app --host 0.0.0.0 --port $Port --no-access-log</arguments>
  <workingdirectory>$e\server</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <depend>Tcpip</depend>
  <serviceaccount><domain>NT AUTHORITY</domain><user>LocalService</user></serviceaccount>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>45 sec</stoptimeout>
  <logpath>$workEsc\logs</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
  <env name="PROJECTOR_DATA_DIR" value="$e\data"/>
  <env name="PROJECTOR_WORK_DIR" value="$workEsc"/>
  <env name="PROJECTOR_SOURCE_ROOT" value="$sourceEsc"/>
  <env name="PROJECTOR_PORT" value="$Port"/>
  <env name="COMFY_URL" value="http://127.0.0.1:8188"/>
  <env name="PROJECTOR_PLANNER_URL" value="http://127.0.0.1:8081/v1"/>
  <env name="PROJECTOR_PLANNER_MODEL" value="local"/>
  <env name="TEMP" value="$workEsc\temp"/>
  <env name="TMP" value="$workEsc\temp"/>
  <env name="PYTHONUTF8" value="1"/>
  <env name="PYTHONDONTWRITEBYTECODE" value="1"/>
</service>
"@ | Set-Content -LiteralPath (Join-Path $serviceRoot 'ProjectorServer.xml') -Encoding UTF8
    function Grant-Access([string]$Path, [string]$Rights) {
        & icacls.exe $Path /grant "*S-1-5-19:$Rights" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not grant service access to $Path" }
    }
    Grant-Access (Join-Path $projectRoot 'server') '(OI)(CI)RX'
    Grant-Access (Join-Path $projectRoot 'web\dist') '(OI)(CI)RX'
    Grant-Access $SourceRoot '(OI)(CI)RX'
    Grant-Access $WorkDir '(OI)(CI)M'
    # Database directory needs journal creation. Do not grant access to signing keys.
    Grant-Access (Join-Path $projectRoot 'data') 'M'
    Get-ChildItem (Join-Path $projectRoot 'data') -File -Filter 'projector.db*' | ForEach-Object { Grant-Access $_.FullName 'M' }
    foreach ($name in @('calibration','import')) {
        $path = Join-Path $projectRoot "data\$name"; New-Item -ItemType Directory -Force $path | Out-Null
        Grant-Access $path '(OI)(CI)M'
    }
    foreach ($name in @('apk','media')) {
        $path = Join-Path $projectRoot "data\$name"
        if (Test-Path $path) { Grant-Access $path '(OI)(CI)RX' }
    }
    if (!$existing) { & $wrapper install; if ($LASTEXITCODE -ne 0) { throw 'Service registration failed' } }
    else { & $wrapper refresh; if ($LASTEXITCODE -ne 0) { throw 'Service refresh failed' } }
    # Let the installing user start/stop this one service without future UAC prompts.
    $descriptor = (& sc.exe sdshow ProjectorServer | Where-Object { $_ -match '^D:' }).Trim()
    if ($LASTEXITCODE -ne 0 -or !$descriptor) { throw 'Cannot read service permissions' }
    if (!$descriptor.Contains($OperatorSid)) {
        $ace = "(A;;CCLCSWRPWPLOCRRC;;;$OperatorSid)"
        $auditIndex = $descriptor.IndexOf('S:')
        if ($auditIndex -ge 0) { $descriptor = $descriptor.Insert($auditIndex, $ace) } else { $descriptor += $ace }
        & sc.exe sdset ProjectorServer $descriptor | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Cannot grant service operator permissions' }
    }
    foreach ($rule in @(@{Name='ProjectorServer-LAN';Protocol='TCP';Port=$Port},@{Name='ProjectorServer-Discovery';Protocol='UDP';Port=5353})) {
        if (!(Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -Name $rule.Name -DisplayName $rule.Name -Direction Inbound -Action Allow -Protocol $rule.Protocol -LocalPort $rule.Port -RemoteAddress LocalSubnet -Profile Private | Out-Null
        }
    }
    # Only hand over a known Projector Uvicorn listener; never stop an unrelated app.
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($owner.CommandLine -notmatch 'uvicorn app\.main:app') { throw "Port $Port is owned by another application" }
        Stop-Process -Id $owner.ProcessId -Force
    }
    Start-Service ProjectorServer
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try { $null = Invoke-RestMethod "http://127.0.0.1:$Port/api/info"; $ready=$true; break } catch { }
    }
    if (!$ready) { throw 'Service did not become healthy; see D:\ProjectorAppData\logs' }
    $before = (Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1).OwningProcess
    $beforeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$before"
    $runner = Invoke-RestMethod "http://127.0.0.1:$Port/api/runner"
    $players = @(Invoke-RestMethod "http://127.0.0.1:$Port/api/devices")
    if ($runner.state -eq 'idle' -and !($players | Where-Object online) -and $beforeProcess.CommandLine -match 'uvicorn app\.main:app') {
        Write-Output 'Testing automatic crash recovery while no show or player is active.'
        Stop-Process -Id $before -Force
        $recovered = $false
        for ($i=0; $i -lt 35; $i++) {
            Start-Sleep -Seconds 2
            try {
                $null = Invoke-RestMethod "http://127.0.0.1:$Port/api/info"
                $after = (Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1).OwningProcess
                if ($after -ne $before) { $recovered=$true; break }
            } catch { }
        }
        if (!$recovered) { throw 'Automatic crash recovery failed' }
        @{before_process=$before;after_process=$after;recovered=$true;checked_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $serviceRoot 'recovery-check.json')
    }
    Get-CimInstance Win32_Service -Filter "Name='ProjectorServer'" | Select-Object Name,State,StartMode,StartName,ProcessId | ConvertTo-Json | Set-Content (Join-Path $serviceRoot 'installed.json')
    Write-Output 'Projector Server installed, running, and available on the private LAN.'
} finally { Stop-Transcript }
