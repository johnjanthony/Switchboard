#Requires -RunAsAdministrator
param()
$ErrorActionPreference = "Stop"

$AppDir = "C:\Work\Switchboard"

Write-Host "Registering SwitchboardSpawn scheduled task..."
$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -File `"$AppDir\scripts\spawn-launcher.ps1`"" -WorkingDirectory $AppDir
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "SwitchboardSpawn" -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Done. Task registered for user '$env:USERNAME'."
