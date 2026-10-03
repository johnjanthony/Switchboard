#!/usr/bin/env bash
# Type-check the switchboard mod (hooks/, types/) against the Claude Code
# function-hook declarations. Claude Code writes those only when it loads a mod
# from a folder you own or when its plugin-authoring skill loads, so this takes
# the newest copy it can find and fails loudly when there is none.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
declarations=""
for root in "${LOCALAPPDATA:-}/Temp/claude/bundled-skills" "${TEMP:-}/claude/bundled-skills"; do
	[ -n "$root" ] || continue
	root="$(cygpath -u "$root" 2>/dev/null || printf '%s' "$root")"
	found="$(ls -t "$root"/*/*/plugin-authoring/types/claude-code.d.ts 2>/dev/null | head -1 || true)"
	if [ -n "$found" ]; then
		declarations="$found"
		break
	fi
done
if [ -z "$declarations" ]; then
	echo "mod-typecheck: no Claude Code declarations found. Load the plugin-authoring skill once in a Claude Code session (it writes them), then re-run." >&2
	exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
to_win() { cygpath -m "$1" 2>/dev/null || printf '%s' "$1"; }
cat > "$work/tsconfig.json" <<EOF
{
	"compilerOptions": {
		"target": "es2023", "lib": ["es2023"], "types": [],
		"module": "esnext", "moduleResolution": "bundler",
		"strict": true, "noUncheckedIndexedAccess": true,
		"noEmit": true, "skipLibCheck": true,
		"jsx": "react", "jsxFactory": "h", "jsxFragmentFactory": "Fragment"
	},
	"include": ["$(to_win "$declarations")", "$(to_win "$repo_root")/hooks", "$(to_win "$repo_root")/types"]
}
EOF
echo "mod-typecheck: using $declarations"
npx -y -p typescript@5 tsc -p "$work/tsconfig.json"
echo "mod-typecheck: clean"
