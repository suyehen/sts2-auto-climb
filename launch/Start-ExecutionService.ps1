$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.runtime/conda/python.exe'
$taskClient = Join-Path $taskRoot 'src/python/tools/runtime/execution_client.py'
& $taskPython $taskClient health 2>$null
if ($LASTEXITCODE -eq 0) { return }
$taskService = Start-Process -FilePath $taskPython -ArgumentList ('"' + (Join-Path $taskRoot 'src/python/tools/runtime/execution_service.py') + '"') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot '.runtime/logs/execution-service.out.log') -RedirectStandardError (Join-Path $taskRoot '.runtime/logs/execution-service.err.log') -PassThru
$taskService.Id | Set-Content (Join-Path $taskRoot '.runtime/local/execution-service.pid')
for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
    Start-Sleep -Milliseconds 100
    & $taskPython $taskClient health 2>$null
    if ($LASTEXITCODE -eq 0) { return }
    if ($taskService.HasExited) { throw 'Execution service failed; see execution-service.err.log' }
}
throw 'Execution service did not become ready'
