param()
$pendingFile = "C:\Work\Switchboard\logs\spawn-pending.json"
if (-not (Test-Path $pendingFile)) { exit 0 }
$params = Get-Content $pendingFile -Raw | ConvertFrom-Json
Remove-Item $pendingFile -Force -ErrorAction SilentlyContinue

if ($params.PSObject.Properties.Name -contains 'agents') {
	# Collab spawn: open one tab per agent
	foreach ($agent in $params.agents) {
		$escapedPath   = $agent.project_path.Replace("'", "''")
		$escapedPrompt = $agent.prompt.Replace("'", "''")
		$command = "Set-Location '$escapedPath'; claude '$escapedPrompt' --dangerously-skip-permissions"
		$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
		Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
		Start-Sleep -Milliseconds 500
	}
} else {
	# Single-agent spawn: existing behaviour
	$escapedPath   = $params.project_path.Replace("'", "''")
	$escapedPrompt = $params.prompt.Replace("'", "''")
	$command = "Set-Location '$escapedPath'; claude '$escapedPrompt' --dangerously-skip-permissions"
	$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
	Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
}
