#Requires -RunAsAdministrator
param(
	[string]$ServiceUser = ""
)
$ErrorActionPreference = "Stop"

$ServiceName      = "switchboard"
$Python           = "C:\Work\Switchboard\.venv\Scripts\python.exe"
$AppDir           = "C:\Work\Switchboard"
$LogDir           = "$AppDir\logs"
$StderrLog        = "$LogDir\nssm-stderr.log"
$HealthUrl        = "http://127.0.0.1:9876/healthz"
$FirewallRuleName = "Switchboard MCP (WSL)"
# HNS draws the per-boot WSL NAT subnet from this pool, so scoping the rule to the
# whole pool survives subnet drift across reboots. The Bearer token (REV-003) is the
# enforced control; this rule is defense-in-depth.
$WslNatPool       = "172.16.0.0/12"

function Invoke-Nssm {
	# Runs nssm and hard-fails the install on a non-zero exit. NEVER route a
	# password-bearing call (ObjectName) through this - it echoes its arguments.
	param([Parameter(Mandatory, ValueFromRemainingArguments)][string[]]$NssmArgs)
	& nssm @NssmArgs
	if ($LASTEXITCODE -ne 0) {
		Write-Host "ERROR: nssm $($NssmArgs -join ' ') failed (exit $LASTEXITCODE)." -ForegroundColor Red
		exit 1
	}
}

function Show-StderrTail {
	if (Test-Path $StderrLog) {
		Write-Host "--- Last 25 lines of $StderrLog ---"
		Get-Content $StderrLog -Tail 25 | ForEach-Object { Write-Host "  $_" }
	}
}

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

# Probe WSL home from Session 1 (where wsl.exe works) - consumed below by the .env
# sanity checks and passed to the service as an env var (the service starts in
# Session 0 where the same probe fails; server/main.py:resolve_wsl_home reads it).
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

# Read SWITCHBOARD_HOST / SWITCHBOARD_TOKEN from .env (mirrors server/config.py:
# _LOOPBACK_HOSTS and require_token_for_nonloopback).
$envPath = Join-Path $AppDir ".env"
$envHost = $null
$envHasToken = $false
if (Test-Path $envPath) {
	$envLines = Get-Content $envPath | Where-Object { $_ -notmatch '^\s*#' }
	$hostLine = $envLines | Where-Object { $_ -match '^\s*SWITCHBOARD_HOST\s*=' } | Select-Object -First 1
	if ($hostLine) { $envHost = ($hostLine -split '=', 2)[1].Trim().Trim('"') }
	$envHasToken = [bool]($envLines | Where-Object { $_ -match '^\s*SWITCHBOARD_TOKEN\s*=\s*\S' })
}
$nonLoopback = [bool]($envHost) -and (@("127.0.0.1", "localhost", "::1") -notcontains $envHost.ToLower())

# REV-003 pairing check: the server refuses a non-loopback bind without
# SWITCHBOARD_TOKEN, so that combination installs a crash-looping service.
# Refuse the install instead - BEFORE any nssm call.
if ($nonLoopback -and -not $envHasToken) {
	Write-Host "ERROR: $envPath sets SWITCHBOARD_HOST=$envHost (non-loopback) but has no SWITCHBOARD_TOKEN." -ForegroundColor Red
	Write-Host "The server fails closed on this pairing (REV-003), so the service would crash-loop."
	Write-Host "Fix before installing:"
	Write-Host "  1. Generate a token:   python -c `"import secrets; print(secrets.token_urlsafe(32))`""
	Write-Host "  2. Add to ${envPath}:  SWITCHBOARD_TOKEN=<value>"
	Write-Host "  3. Update the 1Password-backed env file chezmoi delivers to WSL so WSL clients"
	Write-Host "     send the same token (hooks + MCP registration use Authorization: Bearer)."
	exit 1
}

if (-not (Test-Path $envPath)) {
	Write-Warning "$envPath not found. Copy .env.example -> .env and fill in Firebase credentials before starting the service."
} elseif ($wslHome) {
	if (-not $envHost) {
		Write-Warning "$envPath has no SWITCHBOARD_HOST line - server defaults to 127.0.0.1 (loopback-only), so WSL agents will not be able to reach it. Add SWITCHBOARD_HOST=0.0.0.0 and SWITCHBOARD_TOKEN."
	} elseif (-not $nonLoopback) {
		Write-Warning "$envPath has SWITCHBOARD_HOST=$envHost but WSL was detected - WSL agents will not be able to reach the service. Change to SWITCHBOARD_HOST=0.0.0.0 and set SWITCHBOARD_TOKEN."
	}
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Invoke-Nssm install $ServiceName $Python "-m" "server"
Invoke-Nssm set $ServiceName AppDirectory   $AppDir
Invoke-Nssm set $ServiceName AppStdout      "$LogDir\nssm-stdout.log"
Invoke-Nssm set $ServiceName AppStderr      "$LogDir\nssm-stderr.log"
Invoke-Nssm set $ServiceName AppRotateFiles  1
Invoke-Nssm set $ServiceName AppRotateBytes  5242880
Invoke-Nssm set $ServiceName AppRotateOnline 1
Invoke-Nssm set $ServiceName Description    "Switchboard MCP gateway for Claude Code agents"
Invoke-Nssm set $ServiceName Start          SERVICE_AUTO_START

if ($wslHome) {
	Write-Host "  SWITCHBOARD_WSL_HOME=$wslHome"
	Invoke-Nssm set $ServiceName AppEnvironmentExtra "SWITCHBOARD_WSL_HOME=$wslHome"
} else {
	Write-Warning "WSL home not resolved - service will report wsl_available=false. If WSL is installed, set SWITCHBOARD_WSL_HOME manually via 'nssm set $ServiceName AppEnvironmentExtra ...'."
}

# T-141 residue: create the WSL-pool-scoped inbound rule instead of warning.
# Idempotent by display name; uninstall-service.ps1 removes it.
if ($nonLoopback) {
	Write-Host "Creating firewall rule '$FirewallRuleName' (inbound TCP 9876 from $WslNatPool)..."
	Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
	New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -Action Allow `
		-Protocol TCP -LocalPort 9876 -RemoteAddress $WslNatPool -Profile Any | Out-Null

	# Tail risk made visible: warn when the live WSL subnet has left the pool
	# (conflict-driven HNS fallback - would silently block WSL agents).
	$wslIp = Get-NetIPAddress -InterfaceAlias "vEthernet (WSL*" -AddressFamily IPv4 -ErrorAction SilentlyContinue |
		Select-Object -First 1
	if ($wslIp -and -not ($wslIp.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.')) {
		Write-Warning ("WSL adapter address $($wslIp.IPAddress) is OUTSIDE $WslNatPool - WSL agents will be blocked. " +
			"Fix: Set-NetFirewallRule -DisplayName '$FirewallRuleName' -RemoteAddress '$WslNatPool','<wsl-subnet>'")
	}

	# A broader allow rule for 9876 would silently defeat the scoping - surface it.
	# (Enumerating port filters takes a few seconds; acceptable at install time.)
	$others = Get-NetFirewallPortFilter | Where-Object { $_.LocalPort -eq 9876 } |
		ForEach-Object { $_ | Get-NetFirewallRule } |
		Where-Object { $_.DisplayName -ne $FirewallRuleName -and $_.Enabled -eq "True" -and
			$_.Direction -eq "Inbound" -and $_.Action -eq "Allow" }
	if ($others) {
		Write-Warning "Other enabled inbound allow rule(s) for TCP 9876 defeat the WSL scoping: $(($others | ForEach-Object DisplayName) -join ', ')"
	}
} else {
	Write-Host "SWITCHBOARD_HOST is loopback (or unset) - skipping firewall rule (not needed for a loopback bind)."
}

# The service deliberately runs as LocalSystem (the nssm default): everything the
# server needs arrives by push (hooks, snapshots, MCP calls - see CLAUDE.md,
# "Architectural constraints"), and the Session-1 work (wt.exe tabs) is carried by
# the SwitchboardSpawn scheduled task, which runs as the interactive user regardless
# of the service account. Pass -ServiceUser only when you really want a different
# logon account; the set is verified so a bad account can no longer silently
# fall back to SYSTEM (T-196).
if ($ServiceUser) {
	Write-Host "Setting service logon account to '$ServiceUser'..."
	Write-Host "You will be prompted for the account password."
	$cred = Get-Credential -UserName $ServiceUser -Message "Password for Switchboard service account"
	& nssm set $ServiceName ObjectName $cred.UserName $cred.GetNetworkCredential().Password
	if ($LASTEXITCODE -ne 0) {
		Write-Host "ERROR: nssm set ObjectName failed (exit $LASTEXITCODE). Service remains installed with LocalSystem." -ForegroundColor Red
		exit 1
	}
	$effective = ((& nssm get $ServiceName ObjectName) -join "").Replace("`0", "").Trim()
	if ($effective -ne $cred.UserName) {
		Write-Host "ERROR: ObjectName verification failed - nssm reports '$effective', expected '$($cred.UserName)'." -ForegroundColor Red
		exit 1
	}
} else {
	Write-Host "Service account: LocalSystem (default). Use -ServiceUser to override."
}

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
if ($LASTEXITCODE -ne 0) {
	Write-Host "ERROR: sc.exe sdset failed (exit $LASTEXITCODE)." -ForegroundColor Red
	exit 1
}

Write-Host "Starting $ServiceName..."
Invoke-Nssm start $ServiceName

$deadline = (Get-Date).AddSeconds(30)
$running = $false
$state = ""
while ((Get-Date) -lt $deadline) {
	$state = ((& nssm status $ServiceName) -join "").Replace("`0", "").Trim()
	if ($LASTEXITCODE -eq 0 -and $state -eq "SERVICE_RUNNING") { $running = $true; break }
	Start-Sleep -Milliseconds 250
}
if (-not $running) {
	Write-Host "ERROR: service did not reach SERVICE_RUNNING within 30s (last state: '$state')." -ForegroundColor Red
	Show-StderrTail
	exit 1
}

# SERVICE_RUNNING proves the process started, not that the gateway serves (a
# crash-looping app stays RUNNING under NSSM restart throttling) - /healthz is
# the liveness truth.
$healthy = $false
while ((Get-Date) -lt $deadline) {
	try {
		$resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
		if ($resp.StatusCode -eq 200) { $healthy = $true; break }
	} catch {
		Start-Sleep -Milliseconds 250
	}
}
if (-not $healthy) {
	Write-Host "ERROR: service is SERVICE_RUNNING but $HealthUrl did not return 200 within 30s." -ForegroundColor Red
	Show-StderrTail
	exit 1
}

Write-Host "Done. Service running, /healthz OK. MCP endpoint: http://localhost:9876/mcp"
