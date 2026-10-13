#!/usr/bin/env bash
# The PermissionRequest command hook (hooks/hooks.json). Hands the dialog to the
# switchboard server, which holds the request until John answers the dialog's
# card on his phone, or releases it when the terminal answers first. Prints the
# server's decision, or nothing, which leaves the dialog to the terminal.
#
# The switchboard's own tools never reach this script: hooks.json allows them
# first, by matcher on the tool name, so an agent in away mode can always ask.
#
# Always exits 0: a PermissionRequest hook's exit code 2 is a blocking error.
# Runs under Git Bash on Windows and bash in WSL.

input=$(cat)
base="${SWITCHBOARD_BASE_URL:-http://127.0.0.1:9876}"
auth=()
if [ -n "${SWITCHBOARD_TOKEN:-}" ]; then
	auth=(-H "Authorization: Bearer ${SWITCHBOARD_TOKEN}")
fi
# While the server is down or restarting, resend until the window runs out. The
# window (default 600 s) counts from the start of a failure streak, not from the
# hook's start: a request held for at least the held time (default 30 s) that then
# gets a 503 means the server was up and is restarting, so that starts a new
# streak. The first pause of a streak is 2 s and the rest 5 s
# (SWITCHBOARD_APPROVAL_RETRY_DELAY sets both). A knob that is not a plain number
# gets its default.
window=600
case "${SWITCHBOARD_APPROVAL_RETRY_SECONDS:-}" in
	''|*[!0-9]*) ;;
	*) window=$((10#$SWITCHBOARD_APPROVAL_RETRY_SECONDS)) ;;
esac
held=30
case "${SWITCHBOARD_APPROVAL_HELD_SECONDS:-}" in
	''|*[!0-9]*|0*) ;;
	*) held=$SWITCHBOARD_APPROVAL_HELD_SECONDS ;;
esac
first_pause=2
later_pause=5
if [[ "${SWITCHBOARD_APPROVAL_RETRY_DELAY:-}" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
	first_pause=$SWITCHBOARD_APPROVAL_RETRY_DELAY
	later_pause=$SWITCHBOARD_APPROVAL_RETRY_DELAY
fi
give_up_at=
pause=$first_pause
body=$(mktemp 2>/dev/null) || exit 0
trap 'rm -f "$body"' EXIT

while :; do
	sent_at=$(date +%s)
	status=$(printf '%s' "$input" | curl -s -o "$body" -w '%{http_code}' -X POST \
		-H 'Content-Type: application/json' "${auth[@]}" --data-binary @- "$base/approvals" 2>/dev/null)
	case "$status" in
		200) cat "$body"; exit 0 ;;
		503|000) ;;
		*) exit 0 ;;
	esac
	now=$(date +%s)
	if [ -z "$give_up_at" ] || { [ "$status" = 503 ] && [ $(( now - sent_at )) -ge "$held" ]; }; then
		give_up_at=$(( now + window ))
		pause=$first_pause
	fi
	[ "$now" -lt "$give_up_at" ] || exit 0
	sleep "$pause"
	pause=$later_pause
done
