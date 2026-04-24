#Requires -Version 5.1
param(
	[switch]$Claude,
	[switch]$Gemini
)
$ErrorActionPreference = "Stop"

# If neither flag is set, install to both.
if (-not $Claude -and -not $Gemini) {
	$Claude = $true
	$Gemini = $true
}

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $RepoRoot "scripts\turn-end-hook-away-mode.py"
$PythonExe  = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $ScriptPath)) {
	Write-Error "Hook script not found at $ScriptPath"
	exit 1
}
if (-not (Test-Path $PythonExe)) {
	Write-Error "Python venv not found at $PythonExe"
	exit 1
}

# PS 5.1 does not have ConvertFrom-Json -AsHashtable; walk the PSCustomObject
# tree and rebuild it as nested hashtables / arrays so the merge logic below
# can use .ContainsKey / [] indexing the same way on either PS edition.
#
# Notes on the pipeline hazard: PS 5.1 wraps pipeline values in PSObject, which
# means a plain string like "Bash" reports `-is [PSCustomObject] = True` once it
# has flowed through `ForEach-Object`. That would cause strings to be
# re-serialized as `{Length: N}` objects. Unwrapping `.PSObject.BaseObject` and
# checking for strings / value types first prevents that.
function ConvertTo-HashtableDeep {
	param($InputObject)
	if ($null -eq $InputObject) { return $null }
	$obj = $InputObject.PSObject.BaseObject
	if ($obj -is [string]) { return $obj }
	if ($obj.GetType().IsValueType) { return $obj }
	if ($obj -is [System.Collections.IDictionary]) {
		$h = @{}
		foreach ($key in $obj.Keys) {
			$h[$key] = ConvertTo-HashtableDeep $obj[$key]
		}
		return $h
	}
	if ($obj -is [System.Collections.IEnumerable]) {
		return ,@($obj | ForEach-Object { ConvertTo-HashtableDeep $_ })
	}
	# PSCustomObject (from ConvertFrom-Json) or any other property-bearing object.
	$h = @{}
	foreach ($prop in $InputObject.PSObject.Properties) {
		$h[$prop.Name] = ConvertTo-HashtableDeep $prop.Value
	}
	return $h
}

function Read-SettingsAsHashtable {
	param([string]$Path)
	if (-not (Test-Path $Path)) { return @{} }
	$raw = Get-Content -Raw -Path $Path -Encoding UTF8
	if ($null -eq $raw -or $raw.Trim().Length -eq 0) { return @{} }
	try {
		$parsed = $raw | ConvertFrom-Json -ErrorAction Stop
	} catch {
		Write-Error "Failed to parse ${Path}: $_ -- fix or remove it and retry."
		exit 1
	}
	$ht = ConvertTo-HashtableDeep $parsed
	if ($null -eq $ht -or $ht -isnot [hashtable]) { return @{} }
	return $ht
}

function Backup-Settings {
	param([string]$Path)
	if (-not (Test-Path $Path)) { return }
	$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
	$backup = "$Path.pre-install-$stamp.bak"
	Copy-Item -Path $Path -Destination $backup -Force
	Write-Host "Backed up $Path to $backup"
}

function Format-JsonPretty {
	# PS 5.1's ConvertTo-Json aligns values to the longest key in each object,
	# which looks awful. Serialize compact, then re-indent cleanly with 2 spaces.
	param([string]$CompactJson)
	$sb = New-Object System.Text.StringBuilder
	$depth = 0
	$indent = "  "
	$nl = "`r`n"
	$inString = $false
	$escape = $false
	$pendingNewline = $false

	for ($i = 0; $i -lt $CompactJson.Length; $i++) {
		$c = $CompactJson[$i]
		if ($inString) {
			[void]$sb.Append($c)
			if ($escape) { $escape = $false }
			elseif ($c -eq '\') { $escape = $true }
			elseif ($c -eq '"') { $inString = $false }
			continue
		}
		if ([char]::IsWhiteSpace($c)) { continue }

		if ($c -eq '"') {
			if ($pendingNewline) {
				[void]$sb.Append($nl + ($indent * $depth))
				$pendingNewline = $false
			}
			$inString = $true
			[void]$sb.Append($c)
			continue
		}
		if ($c -eq '{' -or $c -eq '[') {
			if ($pendingNewline) {
				[void]$sb.Append($nl + ($indent * $depth))
				$pendingNewline = $false
			}
			[void]$sb.Append($c)
			$depth++
			$pendingNewline = $true
			continue
		}
		if ($c -eq '}' -or $c -eq ']') {
			$depth--
			if ($pendingNewline) {
				# Empty container: emit immediately, no line break.
				$pendingNewline = $false
			} else {
				[void]$sb.Append($nl + ($indent * $depth))
			}
			[void]$sb.Append($c)
			continue
		}
		if ($c -eq ',') {
			[void]$sb.Append($c)
			$pendingNewline = $true
			continue
		}
		if ($c -eq ':') {
			[void]$sb.Append(': ')
			continue
		}
		# Literal char (number, bool, null).
		if ($pendingNewline) {
			[void]$sb.Append($nl + ($indent * $depth))
			$pendingNewline = $false
		}
		[void]$sb.Append($c)
	}
	return $sb.ToString()
}

function Write-SettingsFromHashtable {
	param([string]$Path, [hashtable]$Data)
	$dir = Split-Path -Parent $Path
	if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
	$compact = $Data | ConvertTo-Json -Depth 20 -Compress
	$pretty = Format-JsonPretty -CompactJson $compact
	# Use .NET to guarantee UTF-8 without BOM on both PS 5.1 and 7+.
	[System.IO.File]::WriteAllText($Path, $pretty + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Remove-ExistingHookEntries {
	param([array]$Entries)
	# Entries is a list of wrapper objects each containing a `hooks` array.
	# Drop any wrapper whose inner hooks reference our script by path fragment.
	$kept = @()
	foreach ($wrapper in $Entries) {
		$inner = $wrapper["hooks"]
		$hasOurs = $false
		if ($null -ne $inner) {
			foreach ($h in $inner) {
				if ($h["command"] -like "*turn-end-hook-away-mode*") {
					$hasOurs = $true
					break
				}
			}
		}
		if (-not $hasOurs) { $kept += $wrapper }
	}
	return ,$kept
}

function Merge-Hook {
	param(
		[string]$SettingsPath,
		[string]$EventName,
		[hashtable]$HookEntry
	)

	Backup-Settings -Path $SettingsPath
	$data = Read-SettingsAsHashtable -Path $SettingsPath

	if (-not $data.ContainsKey("hooks") -or $null -eq $data["hooks"]) {
		$data["hooks"] = @{}
	}
	if (-not $data["hooks"].ContainsKey($EventName) -or $null -eq $data["hooks"][$EventName]) {
		$data["hooks"][$EventName] = @()
	}

	$existing = @($data["hooks"][$EventName])
	$filtered = Remove-ExistingHookEntries -Entries $existing

	$wrapper = @{ hooks = @($HookEntry) }
	$data["hooks"][$EventName] = @($filtered + $wrapper)

	Write-SettingsFromHashtable -Path $SettingsPath -Data $data
	Write-Host "Registered $EventName hook in $SettingsPath"
}

if ($Claude) {
	$claudeCmd = "`"$PythonExe`" `"$ScriptPath`" --cli claude"
	$claudeEntry = @{
		type    = "command"
		command = $claudeCmd
		timeout = 5
	}
	Merge-Hook `
		-SettingsPath "$env:USERPROFILE\.claude\settings.json" `
		-EventName "Stop" `
		-HookEntry $claudeEntry
}

if ($Gemini) {
	# Gemini CLI runs hook commands through the PowerShell script parser on
	# Windows — a quoted path is just a string literal without the call
	# operator `&`. Omitting it produces "Unexpected token" parse errors.
	$geminiCmd = "& `"$PythonExe`" `"$ScriptPath`" --cli gemini"
	$geminiEntry = @{
		name    = "switchboard-away-mode"
		type    = "command"
		command = $geminiCmd
		timeout = 5000
	}
	Merge-Hook `
		-SettingsPath "$env:USERPROFILE\.gemini\settings.json" `
		-EventName "AfterAgent" `
		-HookEntry $geminiEntry
}

Write-Host "Done."
