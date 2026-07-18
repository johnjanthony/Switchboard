param(
	[switch]$SkipTests
)
$ErrorActionPreference = "Stop"

$ServiceName = "switchboard"
$AppDir      = "C:\Work\Switchboard"
$StderrLog   = "$AppDir\logs\nssm-stderr.log"
$HealthUrl   = "http://127.0.0.1:9876/healthz"

function Show-StderrTail {
	if (Test-Path $StderrLog) {
		Write-Host "--- Last 25 lines of $StderrLog ---"
		Get-Content $StderrLog -Tail 25 | ForEach-Object { Write-Host "  $_" }
	}
}

function Get-NssmState {
	# nssm emits UTF-16 output; Windows PowerShell 5.1 captures can interleave NUL
	# chars - strip them before comparing. Exit code is in $LASTEXITCODE afterwards.
	$raw = (& nssm status $ServiceName) -join ""
	return "$raw".Replace("`0", "").Trim()
}

Write-Host "--- Stopping $ServiceName ---"
$state = Get-NssmState
if ($LASTEXITCODE -ne 0) {
	Write-Host "ERROR: nssm status $ServiceName failed (exit ${LASTEXITCODE}): $state" -ForegroundColor Red
	exit 1
}
if ($state -eq "SERVICE_STOPPED") {
	# nssm stop on an already-stopped service exits non-zero; skip it.
	Write-Host "Service already stopped."
} else {
	nssm stop $ServiceName
	if ($LASTEXITCODE -ne 0) {
		Write-Host "ERROR: nssm stop $ServiceName failed (exit $LASTEXITCODE)." -ForegroundColor Red
		exit 1
	}
}

if ($SkipTests) {
	Write-Host "--- Skipping pytest gate (-SkipTests) ---"
} else {
	Write-Host "--- Running pytest gate ---"
	Push-Location $AppDir
	try {
		& ".venv\Scripts\python.exe" -m pytest -q
		if ($LASTEXITCODE -ne 0) {
			Write-Error "Tests failed - $ServiceName NOT restarted. Fix the failures and re-run this script."
			exit 1
		}
	} finally {
		Pop-Location
	}
}

Write-Host "--- Starting $ServiceName ---"
nssm start $ServiceName
if ($LASTEXITCODE -ne 0) {
	Write-Host "ERROR: nssm start $ServiceName failed (exit $LASTEXITCODE)." -ForegroundColor Red
	Show-StderrTail
	exit 1
}

$deadline = (Get-Date).AddSeconds(30)
$running = $false
$state = ""
while ((Get-Date) -lt $deadline) {
	$state = Get-NssmState
	if ($LASTEXITCODE -eq 0 -and $state -eq "SERVICE_RUNNING") { $running = $true; break }
	Start-Sleep -Milliseconds 250
}
if (-not $running) {
	Write-Host "ERROR: service did not reach SERVICE_RUNNING within 30s (last state: '$state')." -ForegroundColor Red
	Show-StderrTail
	exit 1
}

# SERVICE_RUNNING proves the process started, not that the gateway serves: a
# crash-looping app stays RUNNING under NSSM restart throttling. /healthz is the
# liveness truth - and severed MCP clients HANG rather than announcing themselves,
# so nothing client-side can substitute for this poll.
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
