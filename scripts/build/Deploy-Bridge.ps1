param([string]$GamePath = 'E:\steam\steamapps\common\Slay the Spire 2')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (Get-Process -Name SlayTheSpire2,sts2 -ErrorAction SilentlyContinue) { throw 'Close the game before deploying the bridge.' }
if (!(Test-Path -LiteralPath (Join-Path $GamePath 'data_sts2_windows_x86_64/sts2.dll'))) { throw 'Game assembly not found' }
$taskOutput = Join-Path $taskRoot 'src/csharp/bridge/bin/Release/net9.0'
if (!(Test-Path -LiteralPath (Join-Path $taskOutput 'AutoSlayBridge.dll'))) { throw 'Build bridge first' }
$taskLocal = Join-Path $taskRoot '.runtime/local'
New-Item -ItemType Directory -Path $taskLocal -Force | Out-Null
$taskTokenFile = Join-Path $taskLocal 'bridge-token.txt'
if (!(Test-Path -LiteralPath $taskTokenFile)) {
    $taskBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($taskBytes)
    [Convert]::ToHexString($taskBytes) | Set-Content -LiteralPath $taskTokenFile -Encoding ascii -NoNewline
}
$taskDestination = Join-Path $GamePath 'mods/AutoSlayBridge'
New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $taskOutput 'AutoSlayBridge.dll'),(Join-Path $taskOutput 'AutoSlayBridge.json'),$taskTokenFile -Destination $taskDestination
@{url='http://127.0.0.1:18765';token_file=$taskTokenFile} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLocal 'bridge-config.json') -Encoding utf8
$taskManifest = Get-Content -LiteralPath (Join-Path $taskOutput 'AutoSlayBridge.json') -Raw | ConvertFrom-Json
$taskInstallationPath = Join-Path $taskLocal 'installation.json'
$taskInstallation = if (Test-Path -LiteralPath $taskInstallationPath) {
    Get-Content -LiteralPath $taskInstallationPath -Raw | ConvertFrom-Json
} else { [pscustomobject]@{} }
$taskInstallation | Add-Member -NotePropertyName game_path -NotePropertyValue $GamePath -Force
$taskInstallation | Add-Member -NotePropertyName bridge_path -NotePropertyValue $taskDestination -Force
$taskInstallation | Add-Member -NotePropertyName bridge_version -NotePropertyValue $taskManifest.version -Force
$taskInstallation | Add-Member -NotePropertyName deployed_at -NotePropertyValue ([DateTimeOffset]::Now.ToString('o')) -Force
$taskInstallation | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $taskInstallationPath -Encoding utf8
Write-Output "Deployed $($taskManifest.version) bridge to $taskDestination"
