param([string]$GamePath = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (!$GamePath) {
    $taskInstallationFile = Join-Path $taskRoot '.runtime/local/installation.json'
    if (Test-Path -LiteralPath $taskInstallationFile) {
        $GamePath = (Get-Content -LiteralPath $taskInstallationFile -Raw | ConvertFrom-Json).game_path
    }
}
if (!$GamePath) { throw 'Specify -GamePath with the installed Slay the Spire 2 directory.' }
$taskGameData = Join-Path $GamePath 'data_sts2_windows_x86_64'
if (!(Test-Path -LiteralPath (Join-Path $taskGameData 'sts2.dll'))) { throw 'Game assembly not found' }
$env:DOTNET_CLI_HOME = Join-Path $taskRoot '.runtime/cache/dotnet-home'
$env:NUGET_PACKAGES = Join-Path $taskRoot '.runtime/cache/nuget'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
& (Join-Path $taskRoot '.runtime/tools/dotnet/dotnet.exe') restore (Join-Path $taskRoot 'src/csharp/bridge/AutoSlayBridge.csproj') --configfile (Join-Path $taskRoot 'src/csharp/NuGet.Config')
if ($LASTEXITCODE -ne 0) { throw 'Bridge restore failed' }
& (Join-Path $taskRoot '.runtime/tools/dotnet/dotnet.exe') build (Join-Path $taskRoot 'src/csharp/bridge/AutoSlayBridge.csproj') -c Release --no-restore "-p:GameDataDir=$taskGameData"
if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed' }
