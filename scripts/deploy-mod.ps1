<#
.SYNOPSIS
Refreshes the copy of the switchboard plugin that the Claude desktop app runs, and proves it matches the repo.

.DESCRIPTION
Terminal sessions read the plugin live from the marketplace folder. The desktop app runs the copy under
installed_plugins.json's installPath, which only `claude plugin update` refreshes, and only when the plugin's
version changes. plugin.json has no version, so the version is the commit: commit plugin/ first.
Exits 1 at the first failed step.

.PARAMETER Source
The marketplace folder. Defaults to the switchboard marketplace's folder in known_marketplaces.json.
#>
param([string]$Source)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$pluginsDir = Join-Path $env:USERPROFILE '.claude\plugins'
if (-not $Source) {
	$known = Get-Content -Raw (Join-Path $pluginsDir 'known_marketplaces.json') | ConvertFrom-Json
	$Source = $known.switchboard.source.path
}
$pluginDir = Join-Path $Source 'plugin'
if (-not (Test-Path -LiteralPath $pluginDir)) {
	Write-Error "deploy-mod: no plugin folder at $pluginDir"
	exit 1
}

$dirty = git -C $Source status --porcelain -- plugin
if ($LASTEXITCODE -ne 0) {
	Write-Error "deploy-mod: git status failed in $Source"
	exit 1
}
if ($dirty) {
	Write-Error "deploy-mod: plugin/ has uncommitted changes in $Source. The plugin's version is its commit, so update would not copy them. Commit first:`n$($dirty -join "`n")"
	exit 1
}

claude plugin update switchboard@switchboard
if ($LASTEXITCODE -ne 0) {
	Write-Error 'deploy-mod: claude plugin update failed'
	exit 1
}

$installed = Get-Content -Raw (Join-Path $pluginsDir 'installed_plugins.json') | ConvertFrom-Json
$installPath = @($installed.plugins.'switchboard@switchboard')[0].installPath

# Files the CLI writes into a copy that are not in plugin/ (from the T-288 P1 probe).
$cliAdded = @()

function Get-FileHashes([string]$root) {
	$hashes = @{}
	foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Force) {
		$rel = $file.FullName.Substring($root.Length).TrimStart('\', '/')
		$hashes[$rel] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
	}
	return $hashes
}

$want = Get-FileHashes (Resolve-Path -LiteralPath $pluginDir).Path
$have = Get-FileHashes (Resolve-Path -LiteralPath $installPath).Path
$problems = @()
foreach ($rel in $want.Keys) {
	if (-not $have.ContainsKey($rel)) { $problems += "missing from the copy: $rel" }
	elseif ($have[$rel] -ne $want[$rel]) { $problems += "different in the copy: $rel" }
}
foreach ($rel in $have.Keys) {
	if (-not $want.ContainsKey($rel) -and $cliAdded -notcontains $rel) { $problems += "extra in the copy: $rel" }
}
if ($problems.Count -gt 0) {
	Write-Error "deploy-mod: the copy at $installPath does not match ${pluginDir}:`n$(($problems | Sort-Object) -join "`n")"
	exit 1
}

Write-Host "deploy-mod: the desktop app's copy at $installPath matches $pluginDir."
Write-Host 'New desktop sessions load it. Running desktop sessions keep their mod until restarted; /healthz mod.stale_sessions lists them.'
