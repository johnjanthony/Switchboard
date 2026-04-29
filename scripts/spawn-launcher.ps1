param()
$logsDir = "C:\Work\Switchboard\logs"

# Per-spawn unique pending files: glob, sort by creation time, claim each via
# atomic rename so concurrent launcher invocations can't both grab the same one.
$pendingFiles = Get-ChildItem -Path $logsDir -Filter "spawn-pending-*.json" -ErrorAction SilentlyContinue | Sort-Object CreationTime
if (-not $pendingFiles) { exit 0 }

foreach ($f in $pendingFiles) {
	$claimedPath = Join-Path $logsDir ($f.BaseName.Replace("spawn-pending-", "spawn-claimed-") + ".json")
	try {
		# Atomic rename — if another launcher already claimed this file the rename
		# fails and we skip to the next one. -ErrorAction Stop turns the failure
		# into a catchable exception.
		Move-Item -Path $f.FullName -Destination $claimedPath -ErrorAction Stop
	} catch {
		continue
	}

	try {
		$params = Get-Content $claimedPath -Raw | ConvertFrom-Json
	} catch {
		# Malformed JSON — drop the claimed file and move on; nothing to launch.
		Remove-Item $claimedPath -Force -ErrorAction SilentlyContinue
		continue
	}

	if ($params.PSObject.Properties.Name -contains 'agents') {
		# Collab spawn: open one tab per agent
		foreach ($agent in $params.agents) {
			$escapedPath   = $agent.project_path.Replace("'", "''")
			$escapedPrompt = $agent.prompt.Replace("'", "''").Replace('"', '\"')

			$cli = "claude '$escapedPrompt' --dangerously-skip-permissions"
			if ($agent.backend -eq "gemini") {
				$cli = "gemini '$escapedPrompt' --yolo"
			}

			$command = "Set-Location '$escapedPath'; $cli"
			$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
			Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
			Start-Sleep -Milliseconds 500
		}
	} else {
		# Single-agent spawn
		$escapedPath   = $params.project_path.Replace("'", "''")
		$escapedPrompt = $params.prompt.Replace("'", "''").Replace('"', '\"')

		$cli = "claude '$escapedPrompt' --dangerously-skip-permissions"
		if ($params.backend -eq "gemini") {
			$cli = "gemini '$escapedPrompt' --yolo"
		}

		$command = "Set-Location '$escapedPath'; $cli"
		$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
		Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
	}

	Remove-Item $claimedPath -Force -ErrorAction SilentlyContinue
}
