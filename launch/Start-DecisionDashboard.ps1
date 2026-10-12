param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskUrl = 'http://127.0.0.1:18767/'
function Open-DecisionDashboard {
    Write-Output $taskUrl
    if (!$NoBrowser) { Start-Process -FilePath $taskUrl }
}
$taskReady = $false
try {
    $taskResponse = Invoke-WebRequest -Uri $taskUrl -TimeoutSec 2 -UseBasicParsing
    $taskReady = $taskResponse.StatusCode -eq 200
} catch {}
if ($taskReady) { Open-DecisionDashboard; return }
$taskPython = Join-Path $taskRoot '.runtime/conda/python.exe'
$taskScript = Join-Path $taskRoot 'src/python/tools/runtime/decision_dashboard.py'
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList @(('"' + $taskScript + '"'), '--no-browser') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot '.runtime/logs/dashboard.out.log') -RedirectStandardError (Join-Path $taskRoot '.runtime/logs/dashboard.err.log') -PassThru
$taskProcess.Id | Set-Content (Join-Path $taskRoot '.runtime/local/dashboard.pid')
for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
    if ($taskProcess.HasExited) { throw 'Dashboard failed; see dashboard.err.log' }
    try {
        $taskResponse = Invoke-WebRequest -Uri $taskUrl -TimeoutSec 1 -UseBasicParsing
        if ($taskResponse.StatusCode -eq 200) { $taskReady = $true }
    } catch {}
    if ($taskReady) { Open-DecisionDashboard; return }
    Start-Sleep -Milliseconds 100
}
throw 'Dashboard did not become ready; see dashboard.err.log'
