param()
$pendingFile = "C:\Work\Switchboard\logs\spawn-pending.json"
if (-not (Test-Path $pendingFile)) { exit 0 }
$params = Get-Content $pendingFile -Raw | ConvertFrom-Json
Remove-Item $pendingFile -Force -ErrorAction SilentlyContinue
Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "claude", "-p", $params.prompt, "--dangerously-skip-permissions" -WorkingDirectory $params.project_path
