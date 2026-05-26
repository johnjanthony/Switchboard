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

	# Determine the session flag based on spawn type.
	# fresh  → --session-id (mints a new session with a pre-assigned ID)
	# resume → --resume     (restores prior session state from the CLI's local store)
	# combine_resume → --resume (same as resume)
	$sessionFlag = if ($params.type -in @("resume", "combine_resume")) { "--resume" } else { "--session-id" }

	if ($params.PSObject.Properties.Name -contains 'agents') {
		# Structured spawn (fresh / resume / combine_resume): one tab per agent.
		# Each agent specifies its surface ("windows" or "wsl") and cli_session_id.
		foreach ($agent in $params.agents) {
			$surface   = if ($agent.PSObject.Properties.Name -contains 'surface') { $agent.surface } else { "windows" }
			$sessionId = $agent.cli_session_id
			$rawPath   = $agent.project_path
			$rawPrompt = $agent.prompt

			if ($surface -eq "wsl") {
				# Bash single-quote escape: ' → '\''
				$bashSafePath   = $rawPath   -replace "'", "'\''"
				$bashSafePrompt = $rawPrompt -replace "'", "'\''"
				$bashCmd = "cd '$bashSafePath' && claude '$bashSafePrompt' $sessionFlag '$sessionId' --dangerously-skip-permissions"
				Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "wsl", "-e", "bash", "-lc", $bashCmd
			} else {
				# PowerShell single-quote escape: ' → ''
				$psSafePath   = $rawPath   -replace "'", "''"
				$psSafePrompt = $rawPrompt -replace "'", "''"
				$cli = "claude '$psSafePrompt' $sessionFlag '$sessionId' --dangerously-skip-permissions"
				$command = "Set-Location '$psSafePath'; $cli"
				$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
				Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "powershell.exe", "-EncodedCommand", $encoded
			}
			Start-Sleep -Milliseconds 500
		}
	} else {
		# Legacy single-agent spawn (no 'agents' array): backward-compat path.
		# The new flow always writes an 'agents' array; this branch handles pending
		# files written by the pre-Task-25 code during the transition period.
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
