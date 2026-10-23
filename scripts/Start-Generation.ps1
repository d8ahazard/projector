[CmdletBinding()]
param([ValidateSet('comfy','planner')][string]$Service = 'comfy')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:HF_HOME = 'D:\AI\Caches\HuggingFace'
$env:HF_HUB_CACHE = 'D:\AI\Caches\HuggingFace\hub'
$env:TORCH_HOME = 'D:\AI\Caches\Torch'
$env:TEMP = 'D:\AI-temp'
$env:TMP = $env:TEMP
if ($Service -eq 'planner') {
    # The planner unloads its model when idle so ComfyUI can use the GPU.
    & D:\models\opencursor\runtime\llama-server.exe -m D:\models\opencursor\weights\Qwen3-Coder-30B-A3B-IT-Heretic.Q6_K.gguf --alias local --host 127.0.0.1 --port 8081 --n-gpu-layers 99 --ctx-size 8192 --parallel 1 --no-warmup --sleep-idle-seconds 5
} else {
    Set-Location E:\dev\ComfyUI
    & D:\venvs\ComfyUI\Scripts\python.exe main.py --listen 127.0.0.1 --port 8188 --output-directory D:\ProjectorAppData\comfy\output --input-directory D:\ProjectorAppData\comfy\input --temp-directory D:\ProjectorAppData\comfy\temp --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-LTXVideo ComfyUI-Frame-Interpolation ComfyUI-MMAudio
}
