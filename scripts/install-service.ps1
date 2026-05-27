#Requires -RunAsAdministrator
param(
	[string]$ServiceUser = ".$env:USERNAME"
)
$ErrorActionPreference = "Stop"

$ServiceName = "switchboard"
$Python      = "C:\Work\Switchboard\.venv\Scripts\python.exe"
$AppDir      = "C:\Work\Switchboard"
$LogDir      = "$AppDir\logs"

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
	Write-Error "nssm not found on PATH. Run: choco install nssm"
	exit 1
}

if (-not (Test-Path $Python)) {
	Write-Error "Python venv not found at $Python. Run: cd $AppDir && pip install -e '.[dev]'"
	exit 1
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
	Write-Error "Service '$ServiceName' already exists. Run scripts\uninstall-service.ps1 first."
	exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

nssm install  $ServiceName $Python "-m" "server"
nssm set      $ServiceName AppDirectory   $AppDir
nssm set      $ServiceName AppStdout      "$LogDir\nssm-stdout.log"
nssm set      $ServiceName AppStderr      "$LogDir\nssm-stderr.log"
nssm set      $ServiceName AppRotateFiles  1
nssm set      $ServiceName AppRotateBytes  5242880
nssm set      $ServiceName AppRotateOnline 1
nssm set      $ServiceName Description    "Switchboard MCP gateway for Claude Code agents"
nssm set      $ServiceName Start          SERVICE_AUTO_START

# Probe WSL home from Session 1 (where wsl.exe works) and pass to the service as an
# env var — the service starts in Session 0 where the same probe fails. Consumed by
# server/main.py:resolve_wsl_home as its first-priority escape hatch.
Write-Host "Probing WSL home..."
$wslHome = $null
try {
	$probeOutput = & wsl.exe -e bash -lc 'echo $HOME' 2>$null
	if ($LASTEXITCODE -eq 0 -and $probeOutput) {
		$wslHome = "$probeOutput".Trim()
	}
} catch {
	Write-Warning "wsl.exe probe threw: $_"
}
if ($wslHome) {
	Write-Host "  SWITCHBOARD_WSL_HOME=$wslHome"
	nssm set $ServiceName AppEnvironmentExtra "SWITCHBOARD_WSL_HOME=$wslHome"
} else {
	Write-Warning "WSL home not resolved — service will report wsl_available=false. If WSL is installed, set SWITCHBOARD_WSL_HOME manually via 'nssm set $ServiceName AppEnvironmentExtra ...'."
}

# Sanity-check .env. Warnings only — the service can still install/start.
$envPath = Join-Path $AppDir ".env"
if (-not (Test-Path $envPath)) {
	Write-Warning "$envPath not found. Copy .env.example → .env and fill in Firebase credentials before starting the service."
} elseif ($wslHome) {
	$hostLine = Get-Content $envPath | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '^\s*SWITCHBOARD_HOST\s*=' } | Select-Object -First 1
	if (-not $hostLine) {
		Write-Warning "$envPath has no SWITCHBOARD_HOST line — server defaults to 127.0.0.1 (loopback-only), so WSL agents will not be able to reach it. Add SWITCHBOARD_HOST=0.0.0.0 and ensure a firewall inbound rule exists for TCP 9876."
	} elseif ($hostLine -match '^\s*SWITCHBOARD_HOST\s*=\s*"?127\.0\.0\.1"?\s*$') {
		Write-Warning "$envPath has SWITCHBOARD_HOST=127.0.0.1 but WSL was detected — WSL agents will not be able to reach the service. Change to SWITCHBOARD_HOST=0.0.0.0 and ensure a firewall inbound rule exists for TCP 9876."
	}
}

# Run as the interactive user so the SwitchboardSpawn scheduled task runs in the
# user desktop session (Session 1) rather than the service Session 0.
Write-Host "Setting service logon account to '$ServiceUser'..."
Write-Host "You will be prompted for the account password."
$cred = Get-Credential -UserName $ServiceUser -Message "Password for Switchboard service account"
nssm set $ServiceName ObjectName $cred.UserName $cred.GetNetworkCredential().Password

# Register the SwitchboardSpawn scheduled task. The task runs spawn-launcher.ps1
# as the interactive user (LogonType Interactive) so it executes in the user desktop
# session where wt.exe is available. The service triggers it via schtasks /run.
Write-Host "Registering SwitchboardSpawn scheduled task..."
$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -File `"$AppDir\scripts\spawn-launcher.ps1`"" -WorkingDirectory $AppDir
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "SwitchboardSpawn" -Action $action -Principal $principal -Settings $settings -Force | Out-Null

# Grant interactive users start/stop rights without requiring admin for restarts.
# Full rights for BA+SY are included so WRITE_DAC is preserved for future sdset calls.
sc.exe sdset $ServiceName "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCLCSWLOCRRC;;;AU)(A;;CCLCSWRPWPCR;;;IU)"

Write-Host "Starting $ServiceName..."
nssm start $ServiceName
Start-Sleep -Seconds 3
nssm status $ServiceName
Write-Host "Done. MCP endpoint: http://localhost:9876/mcp"
