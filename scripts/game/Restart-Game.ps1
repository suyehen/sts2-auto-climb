param([switch]$Deploy, [switch]$NoContinue,
    [string]$GamePath = 'E:\steam\steamapps\common\Slay the Spire 2',
    [string]$SteamPath = 'E:\steam\Steam.exe')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$taskPython = Join-Path $taskRoot '.runtime/conda/python.exe'
$taskPreviousEpoch = ''
$taskProcesses = @(Get-Process -Name SlayTheSpire2 -ErrorAction SilentlyContinue)
if ($taskProcesses.Count -gt 1) { throw 'Multiple game processes; refuse ambiguous restart.' }
if ($taskProcesses.Count -eq 1) {
    & $taskPython (Join-Path $taskRoot 'src/python/tools/game/exit_game.py')
    if ($LASTEXITCODE -ne 0) { throw 'Normal exit failed or unresolved. No deployment or restart performed.' }
    $taskExitReport = Get-Content -LiteralPath (Join-Path $taskRoot '.runtime/logs/exit-game.json') -Raw | ConvertFrom-Json
    $taskPreviousEpoch = $taskExitReport.session_epoch
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while (Get-Process -Name SlayTheSpire2 -ErrorAction SilentlyContinue) {
        if ([DateTime]::UtcNow -gt $taskDeadline) { throw 'Normal exit did not terminate the process. No force-kill or deployment performed.' }
        Start-Sleep -Milliseconds 250
    }
}
if ($Deploy) { & (Join-Path $taskRoot 'scripts/build/Deploy-Bridge.ps1') -GamePath $GamePath }
$taskManifest = Get-Content -LiteralPath (Join-Path $GamePath 'mods/AutoSlayBridge/AutoSlayBridge.json') -Raw | ConvertFrom-Json
Start-Process -FilePath $SteamPath -ArgumentList '-applaunch','2868840' -WindowStyle Hidden
$taskWaitArgs = @((Join-Path $taskRoot 'src/python/tools/game/wait_for_game.py'), '--version', $taskManifest.version.TrimStart('v'))
if ($taskPreviousEpoch) { $taskWaitArgs += @('--previous-epoch', $taskPreviousEpoch) }
if (!$NoContinue) { $taskWaitArgs += '--continue-save' }
& $taskPython @taskWaitArgs
if ($LASTEXITCODE -ne 0) { throw 'Startup/continue verification failed. Inspect restart-ready.json before further actions.' }
