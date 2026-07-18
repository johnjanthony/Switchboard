# Re-apply after any `graphify export obsidian`: strips leading dots from note names
# (Obsidian hides dot-files) and rewrites wikilinks to match. Collisions get " (method)".
param([string]$Vault = "C:\Work\Switchboard\graphify-out\obsidian")
$all = Get-ChildItem $Vault -Filter *.md -Force
$existing = @{}; $all | ForEach-Object { $existing[$_.Name] = $true }
$map = @{}
foreach ($f in ($all | Where-Object { $_.Name.StartsWith('.') })) {
	$newBase = $f.BaseName -replace '^\.+',''
	if ($existing.ContainsKey("$newBase.md") -or ($map.Values -contains $newBase)) { $newBase = "$newBase (method)" }
	$map[$f.BaseName] = $newBase
}
foreach ($k in $map.Keys) { Move-Item -LiteralPath (Join-Path $Vault "$k.md") -Destination (Join-Path $Vault "$($map[$k]).md") }
$keys = @($map.Keys | Sort-Object Length -Descending)
foreach ($f in Get-ChildItem $Vault -Filter *.md -Force) {
	$text = [IO.File]::ReadAllText($f.FullName); $orig = $text
	foreach ($k in $keys) {
		$text = [regex]::Replace($text, ('\[\[' + [regex]::Escape($k) + '(?=[\]\|#])'), (('[[' + $map[$k]).Replace('$','$$')))
	}
	if ($text -ne $orig) { [IO.File]::WriteAllText($f.FullName, $text) }
}
Write-Host "dedot: renamed $($map.Count) notes in $Vault"
