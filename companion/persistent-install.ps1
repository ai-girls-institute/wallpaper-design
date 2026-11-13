param([Parameter(Mandatory=$true)][string]$RequestPath)
$ErrorActionPreference='Stop'
$request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$receipt=$null
$utf8=[Text.UTF8Encoding]::new($false)
function Resolve-TaskUserSid([string]$UserId) {
    if ($UserId -match '^S-1-') { return $UserId }
    return ([Security.Principal.NTAccount]::new($UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
}
try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator authorization required' }
    if ($identity.User.Value -ne $request.userSid) { throw '温度自动启动需要在当前管理员账户确认UAC；暂不支持借用另一个账户安装。' }
    if ($request.installationId -notmatch '^[0-9a-f]{16}$' -or $request.operation -notin @('install','remove')) { throw 'Invalid installation request' }
    $dataRoot=[IO.Path]::GetFullPath($request.dataRoot)
    $pathCheck=$dataRoot
    while ($pathCheck) {
        if ((Test-Path -LiteralPath $pathCheck) -and ((Get-Item -LiteralPath $pathCheck -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw '安装路径不能包含链接或联接。'}
        $pathCheck=Split-Path -Parent $pathCheck
    }
    if ((Split-Path $dataRoot -Leaf) -ne 'telemetry-runtime') { throw '请选择有效壁纸内的telemetry-runtime目录。' }
    $wallpaper=Split-Path -Parent $dataRoot
    if (-not (Test-Path -LiteralPath (Join-Path $wallpaper 'project.json') -PathType Leaf)) { throw '壁纸目录已不存在。' }
    $receipt=Join-Path $dataRoot 'persistent-install-receipt.json'
    if ((Test-Path -LiteralPath $receipt) -and ((Get-Item -LiteralPath $receipt -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { $receipt=$null; throw 'Receipt path cannot be a link' }
    # Privileged code is copied to a protected directory, never run from user data.
    $base=Join-Path $env:ProgramFiles 'MornyeTelemetrySensors'
    $protected=Join-Path $base $request.installationId
    $sid=$identity.User.Value
    $taskName='MornyeCompanion-Sensors-'+$request.installationId
    $service=New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder=$service.GetFolder('\')
    $existing=$null
    try { $existing=$folder.GetTask($taskName) } catch {
        $failure=$_.Exception
        while ($failure.InnerException) {$failure=$failure.InnerException}
        if ($failure.HResult -ne -2147024894) {throw}
    }
    $description='Mornye protected sensors: '+$dataRoot
    if ($existing -and ($existing.Definition.RegistrationInfo.Description -cne $description -or (Resolve-TaskUserSid $existing.Definition.Principal.UserId) -ne $sid)) { throw '同名温度任务属于其他安装，未修改。' }
    if ($request.operation -eq 'remove') {
        if ($existing) { $existing.Stop(0); $folder.DeleteTask($taskName,0) }
        $result=@{ok=$true;installed=$false;taskName=$taskName;message='温度登录启动已取消；受保护源码保留。'}
    } else {
        $worker=Join-Path $PSScriptRoot 'managed-awcc.ps1'
        $runner=Join-Path $PSScriptRoot 'persistent-runner.ps1'
        if ((Get-FileHash -LiteralPath $worker -Algorithm SHA256).Hash.ToLowerInvariant() -cne 'd0ac0363bdf04cccc9acf56ad81263574345e9d3f78bbb546dfbe3882bfdc657' -or (Get-FileHash -LiteralPath $runner -Algorithm SHA256).Hash.ToLowerInvariant() -cne '8fd6764e6d431f7c0682958b0703e82b6fbe85c82b6e02ca37ab6fe76c84f8be') {throw '内置辅助文件内容不匹配。'}
        foreach ($path in @($base,$protected,$dataRoot,$wallpaper)) {
            if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw '温度安装路径不能是链接或联接。'}
        }
        New-Item -ItemType Directory -Path $protected -Force | Out-Null
        $acl=New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach ($entry in @(@('S-1-5-32-544','FullControl'),@('S-1-5-18','FullControl'),@($sid,'ReadAndExecute'))) {
            $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($entry[0]),$entry[1],'ContainerInherit,ObjectInherit','None','Allow')
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $protected -AclObject $acl
        Copy-Item -LiteralPath $worker -Destination (Join-Path $protected 'managed-awcc.ps1') -Force
        Copy-Item -LiteralPath $runner -Destination (Join-Path $protected 'persistent-runner.ps1') -Force
        $outputRoot=Join-Path $dataRoot 'persistent-alienware'
        $control=Join-Path $dataRoot 'persistent-alienware-control'
        New-Item -ItemType Directory -Path $outputRoot,$control -Force | Out-Null
        $config=@{outputPath=(Join-Path $outputRoot 'alienware-live.json');stopPath=(Join-Path $control 'stop.signal');heartbeatPath=(Join-Path $control 'owner.heartbeat')}
        $configPath=Join-Path $protected 'config.json'
        [IO.File]::WriteAllText($configPath,($config | ConvertTo-Json -Compress),$utf8)
        $d=$service.NewTask(0)
        $d.RegistrationInfo.Description=$description
        $d.Principal.UserId=$sid
        $d.Principal.LogonType=3
        $d.Principal.RunLevel=1
        $d.Settings.ExecutionTimeLimit='PT0S'
        $d.Settings.MultipleInstances=2
        $d.Settings.DisallowStartIfOnBatteries=$false
        $d.Settings.StopIfGoingOnBatteries=$false
        $d.Settings.StartWhenAvailable=$true
        $trigger=$d.Triggers.Create(9)
        $trigger.UserId=$sid
        $trigger.Delay='PT15S'
        $a=$d.Actions.Create(0)
        $a.Path=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
        $a.Arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $protected 'persistent-runner.ps1')+'" -ConfigPath "'+$configPath+'"'
        $a.WorkingDirectory=$protected
        $registered=$folder.RegisterTaskDefinition($taskName,$d,6,$sid,$null,3)
        $null=$registered.Run($null)
        $result=@{ok=$true;installed=$true;taskName=$taskName;userSid=$sid;outputPath=$config.outputPath;protectedCode=$protected;message='温度登录启动已安装。'}
    }
    [IO.File]::WriteAllText($receipt,($result | ConvertTo-Json -Compress),$utf8)
} catch {
    if ($receipt) { [IO.File]::WriteAllText($receipt,(@{ok=$false;installed=$false;message=$_.Exception.Message} | ConvertTo-Json -Compress),$utf8) }
    exit 1
}
