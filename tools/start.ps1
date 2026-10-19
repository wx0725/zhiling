# 一键启动网页：先启动设置里要求启动的 Ollama 服务，再启动网页服务并打开浏览器。
# 由 启动网页.cmd 调用；关闭这个窗口即停止网页服务（Ollama 留在后台，可在任务管理器结束 ollama.exe）。
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
function Test-Url($u) { try { Invoke-RestMethod $u -TimeoutSec 2 | Out-Null; $true } catch { $false } }

# model_settings.json 里要求由本程序启动的 Ollama 服务（没有就什么都不做，见 tools/llm.py）
& (Join-Path $root '.venv\Scripts\python.exe') -B -m tools.llm start

if (Test-Url 'http://127.0.0.1:8790/api/config') {
    Write-Host '网页服务已经在运行。'
    Start-Process 'http://127.0.0.1:8790'
    exit 0
}

Write-Host '启动网页服务：http://127.0.0.1:8790 （关闭此窗口即停止）'
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-RestMethod 'http://127.0.0.1:8790/api/config' -TimeoutSec 1 | Out-Null; Start-Process 'http://127.0.0.1:8790'; break } catch { Start-Sleep 1 }
    }
} | Out-Null
Set-Location $root
& (Join-Path $root '.venv\Scripts\python.exe') -B -m tools.serve
