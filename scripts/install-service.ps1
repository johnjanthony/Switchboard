#Requires -RunAsAdministrator
param()
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

# Grant interactive users start/stop rights without requiring admin for restarts.
# Full rights for BA+SY are included so WRITE_DAC is preserved for future sdset calls.
sc.exe sdset $ServiceName "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCLCSWLOCRRC;;;AU)(A;;CCLCSWRPWPCR;;;IU)"

Write-Host "Starting $ServiceName..."
nssm start $ServiceName
Start-Sleep -Seconds 3
nssm status $ServiceName
Write-Host "Done. MCP endpoint: http://localhost:9876/sse"
