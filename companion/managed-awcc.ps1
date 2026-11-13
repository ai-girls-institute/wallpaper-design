param(
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$StopPath,
    [Parameter(Mandatory = $true)][string]$HeartbeatPath
)
# Production telemetry only. No tests, control calls, or overclocking methods.
# Keep this file UTF-8 with BOM for Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
function Assert-AwccOutputPaths {
    foreach ($publicationPath in @($OutputPath,($OutputPath+'.tmp'),($OutputPath+'.last-publish-error.json'))) {
        $checkPath=[IO.Path]::GetFullPath($publicationPath)
        while ($checkPath) {
            if ((Test-Path -LiteralPath $checkPath) -and ((Get-Item -LiteralPath $checkPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Alienware export path is redirected; publication refused.' }
            $checkPath=Split-Path -Parent $checkPath
        }
    }
}
Assert-AwccOutputPaths
$awccMutex = New-Object System.Threading.Mutex($false, 'Local\MornyeWallpaperAlienwareSensors')
$awccOwnsMutex = $false
try {
    try { $awccOwnsMutex = $awccMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $awccOwnsMutex = $true }
    if (-not $awccOwnsMutex) {
        $blocked = @{schemaVersion=1;timestampMs=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();status='offline';sensors=@();fans=@();message='另一个Alienware读取器正在运行；没有启动重复采集。请使用现有采集或等待它退出。'}
        $blockedDirectory = Split-Path -Parent $OutputPath
        New-Item -ItemType Directory -Path $blockedDirectory -Force | Out-Null
        [IO.File]::WriteAllText($OutputPath, ($blocked | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        exit 17
    }
    $awccDirectory = Split-Path -Parent $OutputPath
    if (-not (Test-Path -LiteralPath $awccDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $awccDirectory -Force | Out-Null
    }
    $awccIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $awccPrincipal = New-Object Security.Principal.WindowsPrincipal($awccIdentity)
    $awccElevated = $awccPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $awccBootId = [Guid]::NewGuid().ToString()
    $awccUTF8 = New-Object System.Text.UTF8Encoding($false)
    $awccCollectorRevision = 'firmware-descriptors-01'

    function Publish-AwccState($State) {
        # Elevated writes stay on the explicitly installed real filesystem path.
        # Refuse redirected output, including an existing file or its ancestors.
        Assert-AwccOutputPaths
        $State['schemaVersion'] = 1
        $State['collectorRevision'] = $awccCollectorRevision
        $State['sessionId'] = $awccBootId
        $State['timestampMs'] = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $State['timestamp'] = [DateTimeOffset]::Now.ToString('o')
        $State['source'] = 'Alienware firmware / AWCC WMI'
        $awccTemporary = $OutputPath + '.tmp'
        [IO.File]::WriteAllText($awccTemporary, ($State | ConvertTo-Json -Depth 8 -Compress), $awccUTF8)
        # PowerShell converts $null to an empty string for a .NET string
        # parameter. NullString passes an actual null backup path instead.
        if ([IO.File]::Exists($OutputPath)) { [IO.File]::Replace($awccTemporary, $OutputPath, [System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($awccTemporary, $OutputPath) }
    }

    function Convert-AwccResourceId([uint32]$Descriptor, [string]$Kind) {
        # Thermal_Information/3 returns SENSOR_ID | 0x0100 for a temperature.
        # Operations 4 and 5 expect only the actual ID in argument byte 1.
        # Keep roles from the enumerated fan/sensor counts, without CPU/GPU guesses.
        if ($Descriptor -gt 511 -or ($Kind -eq 'fan' -and $Descriptor -gt 255)) {
            throw ('Unsupported thermal descriptor: 0x{0:X8} ({1}).' -f $Descriptor, $Kind)
        }
        return [byte]($Descriptor -band 255)
    }

    function Save-AwccPublicationError($Failure) {
        $awccPublicationError = [ordered]@{
            timestamp = [DateTimeOffset]::Now.ToString('o')
            collectorRevision = $awccCollectorRevision
            sessionId = $awccBootId
            message = $Failure.Exception.Message
        }
        try {
            Assert-AwccOutputPaths
            [IO.File]::WriteAllText(($OutputPath + '.last-publish-error.json'), ($awccPublicationError | ConvertTo-Json -Compress), $awccUTF8)
        } catch { Write-Warning 'Alienware export and publication error record could not be written.' }
    }

    function Read-AwccThermal($Instance, [byte]$Operation, [byte]$ResourceId = 0) {
        # Explicit read-only allowlist from kernel.org's AWCC WMI documentation.
        if ($Operation -notin @(2, 3, 4, 5)) { throw 'Thermal operation is not permitted.' }
        $awccArgument = [uint32]$Operation -bor ([uint32]$ResourceId -shl 8)
        $awccResponse = Invoke-CimMethod -InputObject $Instance -MethodName 'Thermal_Information' -Arguments @{arg2 = $awccArgument}
        if ($null -eq $awccResponse.argr) { throw 'Missing thermal response.' }
        return [uint32]$awccResponse.argr
    }

    function Read-AwccFanSensors($Instance, [byte]$Operation, [byte]$FanId, [byte]$Index = 0) {
        if ($Operation -notin @(1, 2)) { throw 'Fan sensor operation is not permitted.' }
        $awccArgument = [uint32]$Operation -bor ([uint32]$FanId -shl 8) -bor ([uint32]$Index -shl 16)
        $awccResponse = Invoke-CimMethod -InputObject $Instance -MethodName 'GetFanSensors' -Arguments @{arg2 = $awccArgument}
        if ($null -eq $awccResponse.argr) { throw 'Missing fan sensor response.' }
        return [uint32]$awccResponse.argr
    }

    if (-not $awccElevated) {
        Publish-AwccState ([ordered]@{status = 'permission_required'; sensors = @(); fans = @(); message = 'Windows 要求管理员权限读取 Alienware 传感器；请完成一次管理员安装。'})
        exit 5
    }
    function Test-ManagedSessionActive([DateTimeOffset]$Started, [string]$StopPath, [string]$HeartbeatPath) {
        if (Test-Path -LiteralPath $StopPath) { return $false }
        if (([DateTimeOffset]::UtcNow - $Started).TotalSeconds -gt 15) {
            if (-not (Test-Path -LiteralPath $HeartbeatPath)) { return $false }
            if (([DateTime]::UtcNow - (Get-Item -LiteralPath $HeartbeatPath).LastWriteTimeUtc).TotalSeconds -gt 12) { return $false }
        }
        return $true
    }
    $managedStarted = [DateTimeOffset]::UtcNow
    while ($true) {
        if (-not (Test-ManagedSessionActive $managedStarted $StopPath $HeartbeatPath)) { break }
        $awccState = [ordered]@{status = 'offline'; sensors = @(); fans = @(); message = '等待 Alienware 温度接口'}
        try {
            $awccInstances = @(Get-CimInstance -Namespace 'root/wmi' -ClassName 'AWCCWmiMethodFunction' | Where-Object { $_.Active })
            if ($awccInstances.Count -ne 1) { throw 'Alienware active interface is missing or ambiguous.' }
            $awccInstance = $awccInstances[0]
            $awccDescription = Read-AwccThermal $awccInstance 2
            if ($awccDescription -eq [uint32]::MaxValue) { throw 'Thermal information is unavailable.' }
            $awccFanCount = $awccDescription -band 255
            $awccSensorCount = ($awccDescription -shr 8) -band 255
            $awccState['thermalDescription'] = '0x{0:X8}' -f $awccDescription
            $awccState['expectedFans'] = $awccFanCount
            $awccState['expectedTemperatures'] = $awccSensorCount
            if ($awccFanCount -gt 16 -or $awccSensorCount -gt 32 -or ($awccFanCount + $awccSensorCount) -eq 0) {
                throw 'Unsupported thermal resource description.'
            }
            $awccFans = @()
            $awccSensors = @()
            $awccResources = @()
            $awccErrors = @()
            $awccIds = @{}
            for ($awccIndex = 0; $awccIndex -lt ($awccFanCount + $awccSensorCount); $awccIndex++) {
                $awccKind = if ($awccIndex -lt $awccFanCount) { 'fan' } else { 'temperature' }
                $awccResource = [ordered]@{index = $awccIndex; kind = $awccKind; descriptor = $null; key = $null}
                try {
                    $awccDescriptor = Read-AwccThermal $awccInstance 3 ([byte]$awccIndex)
                    $awccResource['descriptor'] = '0x{0:X8}' -f $awccDescriptor
                    $awccId = Convert-AwccResourceId $awccDescriptor $awccKind
                    $awccUniqueId = $awccKind + ':' + $awccId
                    if ($awccIds.ContainsKey($awccUniqueId)) { throw 'Duplicate thermal resource ID.' }
                    $awccIds[$awccUniqueId] = $true
                    $awccKey = '0x{0:X2}' -f $awccId
                    $awccResource['key'] = $awccKey
                    if ($awccKind -eq 'fan') {
                        $awccRPM = $null
                        $awccRelated = @()
                        try {
                            $awccRawRPM = Read-AwccThermal $awccInstance 5 ([byte]$awccId)
                            if ($awccRawRPM -gt 60000) { throw ('Invalid fan RPM: {0}.' -f $awccRawRPM) }
                            $awccRPM = [int]$awccRawRPM
                        } catch {
                            $awccErrors += [ordered]@{index = $awccIndex; key = $awccKey; stage = 'rpm'; message = $_.Exception.Message}
                        }
                        try {
                            $awccRelatedCount = Read-AwccFanSensors $awccInstance 1 ([byte]$awccId)
                            if ($awccRelatedCount -gt 32) { throw 'Unsupported fan sensor count.' }
                            for ($awccRelation = 0; $awccRelation -lt $awccRelatedCount; $awccRelation++) {
                                $awccRelatedDescriptor = Read-AwccFanSensors $awccInstance 2 ([byte]$awccId) ([byte]$awccRelation)
                                $awccRelatedId = Convert-AwccResourceId $awccRelatedDescriptor 'temperature'
                                $awccRelated += ('0x{0:X2}' -f $awccRelatedId)
                            }
                        } catch {
                            $awccErrors += [ordered]@{index = $awccIndex; key = $awccKey; stage = 'related_temperatures'; message = $_.Exception.Message}
                        }
                        $awccFans += [ordered]@{id = [int]$awccId; key = $awccKey; rpm = $awccRPM; relatedTemperatureKeys = @($awccRelated); descriptor = $awccResource['descriptor']}
                    } else {
                        $awccTemperature = $null
                        try {
                            $awccRawTemperature = Read-AwccThermal $awccInstance 4 ([byte]$awccId)
                            if ($awccRawTemperature -lt 1 -or $awccRawTemperature -gt 150) { throw ('Invalid temperature: {0}.' -f $awccRawTemperature) }
                            $awccTemperature = [int]$awccRawTemperature
                        } catch {
                            $awccErrors += [ordered]@{index = $awccIndex; key = $awccKey; stage = 'temperature'; message = $_.Exception.Message}
                        }
                        $awccSensors += [ordered]@{id = [int]$awccId; key = $awccKey; valueC = $awccTemperature; label = ('AWCC 温度 ' + $awccKey); descriptor = $awccResource['descriptor']}
                    }
                } catch {
                    $awccErrors += [ordered]@{index = $awccIndex; kind = $awccKind; descriptor = $awccResource['descriptor']; stage = 'enumeration'; message = $_.Exception.Message}
                }
                $awccResources += $awccResource
            }
            $awccState['fans'] = @($awccFans)
            $awccState['sensors'] = @($awccSensors)
            $awccState['resources'] = @($awccResources)
            $awccState['readErrors'] = @($awccErrors)
            $awccValidCount = @($awccFans | Where-Object { $null -ne $_.rpm }).Count + @($awccSensors | Where-Object { $null -ne $_.valueC }).Count
            $awccState['status'] = if ($awccValidCount -eq ($awccFanCount + $awccSensorCount) -and $awccErrors.Count -eq 0) { 'online' } elseif ($awccValidCount -gt 0) { 'partial' } else { 'offline' }
            $awccState['message'] = if ($awccValidCount -gt 0) { 'Alienware 传感器已读取；CPU／GPU 与风扇归属待你确认' } else { 'Alienware 接口尚未返回有效读数' }
            if ($awccErrors.Count -gt 0) { $awccState['message'] += '；部分读取失败：' + $awccErrors[0]['message'] }
        } catch {
            $awccState['message'] = if ($_.Exception -is [System.UnauthorizedAccessException] -or $_.Exception.Message -match 'Access.denied|拒绝访问') { 'Windows 拒绝读取 Alienware 传感器' } else { 'Alienware 接口暂时不可用：' + $_.Exception.Message }
        }
        try { Publish-AwccState $awccState } catch { Save-AwccPublicationError $_ }
        Start-Sleep -Seconds 2
    }
} finally {
    if ($awccOwnsMutex) { $awccMutex.ReleaseMutex() }
    $awccMutex.Dispose()
}
