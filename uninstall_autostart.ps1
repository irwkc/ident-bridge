#Requires -RunAsAdministrator
$TaskName = "IdentBridge"
schtasks /End /TN $TaskName 2>$null | Out-Null
schtasks /Delete /TN $TaskName /F
Write-Host "Task '$TaskName' removed."
