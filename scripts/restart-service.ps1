param(
	[switch]$SkipTests
)
$ErrorActionPreference = "Stop"

$ServiceName = "switchboard"
$AppDir      = "C:\Work\Switchboard"

Write-Host "--- Stopping $ServiceName ---"
nssm stop $ServiceName

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
Start-Sleep -Seconds 3
nssm status $ServiceName
Write-Host "Done. MCP endpoint: http://localhost:9876/mcp"
