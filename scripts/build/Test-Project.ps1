$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$env:DOTNET_CLI_HOME = Join-Path $taskRoot '.runtime/cache/dotnet-home'
$env:NUGET_PACKAGES = Join-Path $taskRoot '.runtime/cache/nuget'
$taskDotnet = Join-Path $taskRoot '.runtime/tools/dotnet/dotnet.exe'
$taskTests = Join-Path $taskRoot 'src/csharp/tests/CommandTests'
& $taskDotnet restore $taskTests --configfile (Join-Path $taskRoot 'src/csharp/NuGet.Config')
if ($LASTEXITCODE -ne 0) { throw 'Test restore failed' }
& $taskDotnet run --no-restore --project $taskTests
if ($LASTEXITCODE -ne 0) { throw 'Command tests failed' }
$taskPytestTemp = Join-Path $taskRoot ('.runtime/cache/pytest-run-' + [guid]::NewGuid().ToString('N'))
& (Join-Path $taskRoot '.runtime/conda/python.exe') -m pytest -c (Join-Path $taskRoot 'src/python/pyproject.toml') (Join-Path $taskRoot 'src/python/tests') -q --basetemp $taskPytestTemp
if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
