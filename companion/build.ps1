param([string]$Python='')
$ErrorActionPreference='Stop'
$sourceRoot=$PSScriptRoot
if (-not $Python) {$Python=Join-Path $sourceRoot '.build-env/Scripts/python.exe'}
& $Python -B (Join-Path $sourceRoot 'prepare-resources.py')
if ($LASTEXITCODE) {exit $LASTEXITCODE}
& $Python -B -m PyInstaller --onefile --windowed --noupx --name Mornye-Telemetry --paths $sourceRoot --add-data ((Join-Path $sourceRoot 'configure.html')+':.') --add-data ((Join-Path $sourceRoot 'managed-awcc.ps1')+':.') --add-data ((Join-Path $sourceRoot 'startup-task.ps1')+':.') --add-data ((Join-Path $sourceRoot 'persistent-install.ps1')+':.') --add-data ((Join-Path $sourceRoot 'persistent-runner.ps1')+':.') --distpath (Join-Path $sourceRoot 'dist') --workpath (Join-Path $sourceRoot 'build') --specpath (Join-Path $sourceRoot 'build') (Join-Path $sourceRoot 'launcher.py')
exit $LASTEXITCODE
