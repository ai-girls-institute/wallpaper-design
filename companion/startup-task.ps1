param([Parameter(Mandatory=$true)][string]$RequestPath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($request.taskName -notmatch '^MornyeCompanion-Login-[0-9a-f]{16}$') { throw 'Invalid companion task name' }
if ($request.action -notin @('install','remove','status','run')) { throw 'Invalid task operation' }
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Resolve-TaskUserSid([string]$UserId) {
    if ($UserId -match '^S-1-') { return $UserId }
    return ([Security.Principal.NTAccount]::new($UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
}
$service=New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder=$service.GetFolder('\')
$existing=$null
try { $existing=$folder.GetTask($request.taskName) }
catch {
    $failure=$_.Exception
    while ($failure.InnerException) { $failure=$failure.InnerException }
    if ($failure.HResult -ne -2147024894) { throw }
}
if ($existing) {
    $d=$existing.Definition
    if ($d.RegistrationInfo.Description -cne $request.description -or (Resolve-TaskUserSid $d.Principal.UserId) -ne $sid -or $d.Principal.RunLevel -ne 0 -or $d.Actions.Count -ne 1) { throw '同名任务不属于本次普通采集器，未修改。' }
    if ($request.action -ne 'install' -and ($d.Actions.Item(1).Path -cne $request.executable -or $d.Actions.Item(1).Arguments -cne $request.arguments)) { throw '采集器任务路径不匹配，未修改。' }
}
if ($request.action -eq 'install') {
    if (-not (Test-Path -LiteralPath $request.executable -PathType Leaf)) { throw 'EXE is missing' }
    $d=$service.NewTask(0)
    $d.RegistrationInfo.Description=$request.description
    $d.Principal.UserId=$sid
    $d.Principal.LogonType=3
    $d.Principal.RunLevel=0
    $d.Settings.ExecutionTimeLimit='PT0S'
    $d.Settings.MultipleInstances=2
    $d.Settings.DisallowStartIfOnBatteries=$false
    $d.Settings.StopIfGoingOnBatteries=$false
    $d.Settings.StartWhenAvailable=$true
    $d.Settings.AllowDemandStart=$true
    $d.Settings.WakeToRun=$false
    $trigger=$d.Triggers.Create(9)
    $trigger.UserId=$sid
    $trigger.Delay='PT20S'
    $action=$d.Actions.Create(0)
    $action.Path=$request.executable
    $action.Arguments=$request.arguments
    $action.WorkingDirectory=Split-Path -Parent $request.executable
    $registered=$folder.RegisterTaskDefinition($request.taskName,$d,6,$sid,$null,3)
    # No periodic trigger: intentional Exit stays stopped until next login.
    @{installed=$true;taskName=$request.taskName;userSid=$sid;logonDelaySeconds=20;runLevel='Limited'} | ConvertTo-Json -Compress
} elseif ($request.action -eq 'remove') {
    # Removing login registration does not kill the currently running GUI.
    if ($existing) { $folder.DeleteTask($request.taskName,0) }
    @{installed=$false;taskName=$request.taskName} | ConvertTo-Json -Compress
} elseif ($request.action -eq 'run') {
    if (-not $existing) { throw 'Task missing' }
    $null=$existing.Run($null)
    @{installed=$true;taskName=$request.taskName} | ConvertTo-Json -Compress
} else {
    @{installed=($null -ne $existing);taskName=$request.taskName;xml=$(if ($existing) {$existing.Xml} else {''})} | ConvertTo-Json -Compress
}
