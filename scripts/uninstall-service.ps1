#Requires -RunAsAdministrator
param()
$ErrorActionPreference = "Stop"

$ServiceName      = "switchboard"
$FirewallRuleName = "Switchboard MCP (WSL)"

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -eq $svc) {
	Write-Host "Service '$ServiceName' not found - skipping service removal."
} else {
	if ($svc.Status -ne "Stopped") {
		Write-Host "Stopping $ServiceName..."
		nssm stop $ServiceName
		if ($LASTEXITCODE -ne 0) {
			Write-Host "ERROR: nssm stop $ServiceName failed (exit $LASTEXITCODE)." -ForegroundColor Red
			exit 1
		}
	}
	Write-Host "Removing $ServiceName..."
	nssm remove $ServiceName confirm
	if ($LASTEXITCODE -ne 0) {
		Write-Host "ERROR: nssm remove $ServiceName failed (exit $LASTEXITCODE)." -ForegroundColor Red
		exit 1
	}
}

$task = Get-ScheduledTask -TaskName "SwitchboardSpawn" -ErrorAction SilentlyContinue
if ($null -ne $task) {
	Write-Host "Removing SwitchboardSpawn scheduled task..."
	Unregister-ScheduledTask -TaskName "SwitchboardSpawn" -Confirm:$false
}

$rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
if ($null -ne $rule) {
	Write-Host "Removing firewall rule '$FirewallRuleName'..."
	$rule | Remove-NetFirewallRule
}

Write-Host "Done."
