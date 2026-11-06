#Requires -RunAsAdministrator
# powershell -ExecutionPolicy Bypass -File C:\ident-bridge\install_autostart.ps1

$ErrorActionPreference = "Stop"

$TaskName = "IdentBridge"
$WorkDir  = "C:\ident-bridge"
$Vbs      = Join-Path $WorkDir "run_hidden.vbs"
$Forever  = Join-Path $WorkDir "run_forever.cmd"
$Python   = Join-Path $WorkDir "venv\Scripts\python.exe"
$App      = Join-Path $WorkDir "app.py"
$WScript  = Join-Path $env:SystemRoot "System32\wscript.exe"

foreach ($p in @($Vbs, $Forever, $Python, $App, $WScript)) {
    if (-not (Test-Path $p)) { throw "File not found: $p" }
}

# Stop old instances so port 8000 is free
Write-Host "Stopping old processes..."
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$action = New-ScheduledTaskAction `
    -Execute $WScript `
    -Argument "`"$Vbs`"" `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -MultipleInstances IgnoreNew `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

$task  = Get-ScheduledTask -TaskName $TaskName
$info  = Get-ScheduledTaskInfo -TaskName $TaskName
$procs = Get-Process python -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Task state : $($task.State)"
Write-Host "Last result: $($info.LastTaskResult)  (0 = OK)"
Write-Host "Last run   : $($info.LastRunTime)"

if ($procs) {
    Write-Host "OK: python running in background (PID $($procs.Id -join ', '))"
    Write-Host "No console window needed. Close any old black windows."
    Write-Host "Check log: C:\ident-bridge\bridge.log"
} else {
    Write-Host "FAIL: python still not running."
    Write-Host "Open: C:\ident-bridge\bridge.log"
    Write-Host "And:  C:\ident-bridge\check_status.cmd"
}
