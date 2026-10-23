[CmdletBinding()]
param(
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 8000,
    [string]$WorkDir = 'D:\ProjectorAppData',
    [string]$SourceRoot = 'D:\ProjectionPacks'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PROJECTOR_DATA_DIR = Join-Path $projectRoot 'data'
$env:PROJECTOR_WORK_DIR = $WorkDir
$env:PROJECTOR_SOURCE_ROOT = $SourceRoot
$env:PROJECTOR_PORT = "$Port"
$env:COMFY_URL = 'http://127.0.0.1:8188'
$env:PROJECTOR_PLANNER_URL = 'http://127.0.0.1:8081/v1'
$env:TEMP = Join-Path $WorkDir 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP,$SourceRoot | Out-Null
Set-Location (Join-Path $projectRoot 'server')
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $BindAddress --port $Port
