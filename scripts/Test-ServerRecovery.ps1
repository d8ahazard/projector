[CmdletBinding()]
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator rights required for crash simulation' }
    $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated" -PassThru
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw 'Recovery check failed; see D:\ProjectorAppData\service\recovery-test.log' }
    return
}
Start-Transcript -Path D:\ProjectorAppData\service\recovery-test.log -Append
try {
    $service = Get-CimInstance Win32_Service -Filter "Name='ProjectorServer'"
    if ($service.State -ne 'Running') { throw 'Service must be running' }
    $state = Invoke-RestMethod http://127.0.0.1:8000/api/runner
    $devices = @(Invoke-RestMethod http://127.0.0.1:8000/api/devices)
    if ($state.state -ne 'idle' -or ($devices | Where-Object online)) { throw 'Run this check only when no show or player is active' }
    $before = (Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -First 1).OwningProcess
    $ancestor = $before; $isChild = $false
    for ($i=0; $i -lt 5; $i++) {
        if ($ancestor -eq $service.ProcessId) { $isChild=$true; break }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ancestor"
        if (!$proc) { break }; $ancestor=$proc.ParentProcessId
    }
    if (!$isChild) { throw 'Listener is not a child of ProjectorServer' }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    Stop-Process -Id $before -Force
    $recovered = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try {
            $null=Invoke-RestMethod http://127.0.0.1:8000/api/info -TimeoutSec 2
            $after=(Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -First 1).OwningProcess
            if ($after -ne $before) { $recovered=$true; break }
        } catch { }
    }
    if (!$recovered) { throw 'Automatic recovery did not restore the API' }
    @{before_process=$before;after_process=$after;recovered=$true;seconds=$watch.Elapsed.TotalSeconds;checked_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content D:\ProjectorAppData\service\recovery-check.json
    Get-CimInstance Win32_Service -Filter "Name='ProjectorServer'" | Select-Object Name,State,StartMode,StartName,ProcessId | ConvertTo-Json | Set-Content D:\ProjectorAppData\service\installed.json
    Write-Output 'Crash recovery passed.'
} finally { Stop-Transcript }
