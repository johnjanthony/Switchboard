#Requires -RunAsAdministrator
param()
$ErrorActionPreference = "Stop"

$ServiceName = "switchboard"

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -eq $svc) {
	Write-Host "Service '$ServiceName' not found — nothing to remove."
	exit 0
}

Write-Host "Stopping $ServiceName..."
nssm stop $ServiceName
Write-Host "Removing $ServiceName..."
nssm remove $ServiceName confirm
Write-Host "Done."
