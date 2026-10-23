[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProgramId,
    [int]$DurationSeconds = 180,
    [string]$Server = 'http://127.0.0.1:8000',
    [string]$OutputDirectory = (Join-Path 'D:\ProjectorAppData\validation' ("playback-" + (Get-Date -Format 'yyyyMMdd-HHmmss')))
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

function Get-Players {
    $response = Invoke-RestMethod "$Server/api/devices"
    @($response) | Where-Object role -eq 'player'
}

function Metric([object]$device, [string]$name) {
    $value = $device.status.management.$name
    if ($null -eq $value) { return 0 }
    return [long]$value
}

$program = Invoke-RestMethod "$Server/api/programs/$ProgramId"
$preflight = Invoke-RestMethod "$Server/api/programs/$ProgramId/preflight"
$before = Get-Players
$program | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'program.json') -Encoding utf8
$preflight | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'preflight.json') -Encoding utf8
$before | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'devices-before.json') -Encoding utf8

$samples = [System.Collections.Generic.List[object]]::new()
$started = $false
try {
    $start = Invoke-RestMethod -Method Post -Uri "$Server/api/runner/start" -ContentType 'application/json' -Body (@{program_id=$ProgramId} | ConvertTo-Json)
    $start | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'runner-start.json') -Encoding utf8
    $started = $true
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = 0
    while ($clock.Elapsed.TotalSeconds -lt $DurationSeconds) {
        $devices = Get-Players
        $runner = Invoke-RestMethod "$Server/api/runner"
        $sample = [pscustomobject]@{
            utc = [DateTime]::UtcNow.ToString('o')
            elapsed_s = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
            runner_state = $runner.state
            sync_progress = $runner.sync_progress
            devices = @($devices | ForEach-Object {
                [pscustomobject]@{
                    id = $_.id; name = $_.name; online = $_.online
                    playback_state = $_.status.management.playback_state
                    asset_id = $_.status.asset_id; event_id = $_.status.event_id
                    drift_ms = $_.status.drift_ms
                    loaded_decoders = Metric $_ 'loaded_decoders'
                    clip_loads = Metric $_ 'clip_loads'
                    buffer_events = Metric $_ 'buffer_events'
                    buffer_ms = Metric $_ 'buffer_ms'
                    dropped_frames = Metric $_ 'dropped_frames'
                    playback_errors = Metric $_ 'playback_errors'
                    storage_free_mb = $_.status.storage_free_mb
                }
            })
        }
        $samples.Add($sample)
        $sample | ConvertTo-Json -Depth 10 -Compress | Add-Content (Join-Path $OutputDirectory 'samples.ndjson') -Encoding utf8
        if ($clock.Elapsed.TotalSeconds -ge $nextProgress) {
            $playing = @($sample.devices | Where-Object playback_state -eq 'playing').Count
            $loads = ($sample.devices | Measure-Object clip_loads -Sum).Sum
            $buffers = ($sample.devices | Measure-Object buffer_events -Sum).Sum
            $drops = ($sample.devices | Measure-Object dropped_frames -Sum).Sum
            Write-Host ("{0,4:n0}s  playing={1}  loads={2}  stalls={3}  dropped={4}" -f $clock.Elapsed.TotalSeconds,$playing,$loads,$buffers,$drops)
            $nextProgress += 30
        }
        Start-Sleep -Seconds 2
    }
}
finally {
    if ($started) {
        Invoke-RestMethod -Method Post -Uri "$Server/api/runner/stop" | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'runner-stop.json') -Encoding utf8
        Start-Sleep -Seconds 5
    }
}

$after = Get-Players
$after | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'devices-after.json') -Encoding utf8
$deviceReports = @()
foreach ($end in $after) {
    $begin = $before | Where-Object id -eq $end.id | Select-Object -First 1
    $playingSamples = @($samples | ForEach-Object devices | Where-Object {$_.id -eq $end.id -and $_.playback_state -eq 'playing'})
    $drifts = @($playingSamples | ForEach-Object {[math]::Abs([double]($_.drift_ms ?? 0))})
    $deviceReports += [pscustomobject]@{
        id = $end.id; name = $end.name
        online_after = $end.online
        standby_after = $end.status.management.playback_state -eq 'standby'
        decoders_after = Metric $end 'loaded_decoders'
        clip_loads = (Metric $end 'clip_loads') - (Metric $begin 'clip_loads')
        buffer_events = (Metric $end 'buffer_events') - (Metric $begin 'buffer_events')
        buffer_ms = (Metric $end 'buffer_ms') - (Metric $begin 'buffer_ms')
        dropped_frames = (Metric $end 'dropped_frames') - (Metric $begin 'dropped_frames')
        playback_errors = (Metric $end 'playback_errors') - (Metric $begin 'playback_errors')
        max_abs_drift_ms = if ($drifts.Count) { ($drifts | Measure-Object -Maximum).Maximum } else { 0 }
        unique_assets = @($playingSamples.asset_id | Where-Object {$_} | Sort-Object -Unique)
        free_mb_before = $begin.status.storage_free_mb
        free_mb_after = $end.status.storage_free_mb
    }
}
$report = [pscustomobject]@{
    generated_at = [DateTime]::UtcNow.ToString('o')
    program_id = $ProgramId; program_name = $program.name
    requested_duration_seconds = $DurationSeconds
    samples = $samples.Count
    samples_with_both_playing = @($samples | Where-Object {@($_.devices | Where-Object playback_state -eq 'playing').Count -eq $before.Count}).Count
    preflight_errors = @($preflight.errors)
    preflight_warnings = @($preflight.warnings)
    required_bytes = $preflight.required_bytes
    devices = $deviceReports
}
$report | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $OutputDirectory 'report.json') -Encoding utf8
$report | ConvertTo-Json -Depth 20
