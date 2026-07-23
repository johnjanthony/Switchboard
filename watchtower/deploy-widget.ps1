# Deploy the Watchtower widget: quit the running instance, publish the Release
# single-file EXE, and relaunch it. One-shot replacement for the three manual
# steps in README.md (quit via tray -> dotnet publish -> relaunch).
#
# Run from anywhere; paths resolve relative to this script's own directory.
#   .\deploy-widget.ps1              # stop, publish, relaunch
#   .\deploy-widget.ps1 -NoLaunch    # stop and publish, but do not relaunch
param(
	[switch]$NoLaunch
)
$ErrorActionPreference = "Stop"

$Root       = $PSScriptRoot
$Csproj     = Join-Path $Root "src\Switchboard.Watchtower\Switchboard.Watchtower.csproj"
$PublishDir = Join-Path $Root "publish"
$Exe        = Join-Path $PublishDir "Switchboard.Watchtower.exe"
$ProcName   = "Switchboard.Watchtower"

# The running EXE is locked while it runs (and a named mutex allows only one
# instance), so publishing over it fails until the process exits. Stop it and
# wait for the handle to release before publishing.
Write-Host "--- Stopping running widget ($ProcName) ---"
$procs = Get-Process -Name $ProcName -ErrorAction SilentlyContinue
if ($procs) {
	$procs | Stop-Process -Force
	foreach ($p in $procs) { $p.WaitForExit(10000) | Out-Null }
	Start-Sleep -Milliseconds 1000   # brief grace for the file handle and named mutex to drop
	Write-Host "  stopped $($procs.Count) instance(s)."
} else {
	Write-Host "  none running."
}

Write-Host "--- Publishing Release single-file EXE ---"
& dotnet publish $Csproj -c Release -r win-x64 -p:PublishSingleFile=true --self-contained true -o $PublishDir
if ($LASTEXITCODE -ne 0) {
	Write-Host "ERROR: dotnet publish failed (exit $LASTEXITCODE). If it is a file lock, ensure the widget is fully stopped and retry." -ForegroundColor Red
	exit 1
}
if (-not (Test-Path $Exe)) {
	Write-Host "ERROR: publish reported success but $Exe is missing." -ForegroundColor Red
	exit 1
}

if ($NoLaunch) {
	Write-Host "Done. Published to $Exe (not relaunched; -NoLaunch)."
	exit 0
}

Write-Host "--- Relaunching widget ---"
Start-Process -FilePath $Exe
Start-Sleep -Milliseconds 1500
if (Get-Process -Name $ProcName -ErrorAction SilentlyContinue) {
	Write-Host "Done. Widget relaunched from $Exe" -ForegroundColor Green
} else {
	Write-Host "WARNING: launched $Exe but no $ProcName process is visible yet - check the taskbar." -ForegroundColor Yellow
}
