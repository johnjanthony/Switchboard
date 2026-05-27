param()
$logsDir = "C:\Work\Switchboard\logs"
$launcherLog = Join-Path $logsDir "spawn-launcher.log"

function Write-LauncherLog {
	param([string]$msg)
	$ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
	Add-Content -Path $launcherLog -Value "[$ts] $msg" -ErrorAction SilentlyContinue
}

Write-LauncherLog "launcher start (pid=$PID user=$env:USERNAME)"

# Per-spawn unique pending files: glob, sort by creation time, claim each via
# atomic rename so concurrent launcher invocations can't both grab the same one.
$pendingFiles = Get-ChildItem -Path $logsDir -Filter "spawn-pending-*.json" -ErrorAction SilentlyContinue | Sort-Object CreationTime
if (-not $pendingFiles) {
	Write-LauncherLog "no pending files"
	exit 0
}
Write-LauncherLog "found $($pendingFiles.Count) pending file(s)"

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

			Write-LauncherLog "agent[$([Array]::IndexOf($params.agents, $agent))] surface=$surface session=$sessionId path='$rawPath' prompt-bytes=$($rawPrompt.Length)"
			if ($surface -eq "wsl") {
				# Versioned static script + per-spawn prompt file. The launcher
				# writes only the prompt (short text, no quoting required) to
				# `logs/spawn-prompt-<uuid>.txt`. The static script
				# `scripts/spawn-claude-wsl.sh` reads the prompt, deletes the
				# file, and invokes claude. Workspace path, session flag, and
				# session id pass as simple positional args.
				#
				# Why this pattern: earlier attempts (long `bash -lc "<cmd>"`,
				# base64 wrappers) failed because wt does NOT preserve outer
				# double-quoting when forwarding long quoted args to the new
				# tab's process — wsl received the wrapper as multiple tokens
				# and bash effectively ran a no-op echo. With a versioned
				# script, only short args traverse wt's tokenization; the
				# complex content lives in files.
				#
				# Diagnostic lines bracket the claude invocation so a
				# flash-and-disappear tab still leaves a trace under
				# C:\Work\Switchboard\logs\spawn-wsl.log. Tail after a failed
				# WSL spawn:
				#   wsl -e tail -50 /mnt/c/Work/Switchboard/logs/spawn-wsl.log

				# Write the prompt to a one-shot file (deleted by static script
				# after read). LF line endings, UTF-8 no BOM.
				$promptId = [guid]::NewGuid().ToString("N")
				$promptWinPath = Join-Path $logsDir "spawn-prompt-$promptId.txt"
				$promptWslPath = "/mnt/c/Work/Switchboard/logs/spawn-prompt-$promptId.txt"
				$promptForFile = $rawPrompt -replace "`r`n", "`n"
				[System.IO.File]::WriteAllText($promptWinPath, $promptForFile, [System.Text.UTF8Encoding]::new($false))

				# Static script lives in the plugin's scripts/ directory.
				$staticScriptWsl = "/mnt/c/Work/Switchboard/scripts/spawn-claude-wsl.sh"

				Write-LauncherLog "wsl spawn (static-script): wt new-tab -- wsl.exe -e bash -l $staticScriptWsl '$rawPath' $sessionFlag $sessionId $promptWslPath (prompt-bytes=$($promptForFile.Length))"
				try {
					$proc = Start-Process -FilePath "wt" -ArgumentList "new-tab", "--", "wsl.exe", "-e", "bash", "-l", $staticScriptWsl, $rawPath, $sessionFlag, $sessionId, $promptWslPath -PassThru -ErrorAction Stop
					Write-LauncherLog "wsl Start-Process OK pid=$($proc.Id) prompt=$promptWslPath"
				} catch {
					Write-LauncherLog "wsl Start-Process FAILED: $_"
				}
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
