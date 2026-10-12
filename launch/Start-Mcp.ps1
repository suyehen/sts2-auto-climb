$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Push-Location $taskRoot
try { & (Join-Path $taskRoot '.runtime/conda/python.exe') (Join-Path $taskRoot 'src/python/src/autoslay/mcp/server.py') } finally { Pop-Location }
