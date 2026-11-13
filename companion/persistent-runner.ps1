param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference='Stop'
$config=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$worker=Join-Path $PSScriptRoot 'managed-awcc.ps1'
function Test-OwnerLease([string]$StopPath,[string]$HeartbeatPath) {
    if (Test-Path -LiteralPath $StopPath) { return $false }
    if (-not (Test-Path -LiteralPath $HeartbeatPath -PathType Leaf)) { return $false }
    return (([DateTime]::UtcNow - (Get-Item -LiteralPath $HeartbeatPath).LastWriteTimeUtc).TotalSeconds -le 12)
}
while ($true) {
    if (Test-OwnerLease $config.stopPath $config.heartbeatPath) {
        & $worker -OutputPath $config.outputPath -StopPath $config.stopPath -HeartbeatPath $config.heartbeatPath
    }
    Start-Sleep -Seconds 2
}
