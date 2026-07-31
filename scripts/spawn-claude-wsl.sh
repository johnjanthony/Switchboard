#!/usr/bin/env bash
# Spawns a claude session inside WSL for Switchboard.
#
# Invoked by spawn-launcher.ps1 via:
#   wt new-tab -- wsl.exe -e bash -l \
#     /mnt/c/Work/Switchboard/scripts/spawn-claude-wsl.sh \
#     <workspace-path> <session-flag> <session-id> <prompt-file>
#
# Args:
#   $1  workspace path (absolute, WSL filesystem; e.g. /home/janthony/work/rpdm)
#   $2  session flag (--session-id for fresh, --resume for resume/combine_resume)
#   $3  session id (UUID)
#   $4  prompt file path (absolute, WSL form; e.g. /mnt/c/.../spawn-prompt-<id>.txt)
#       Content is the prompt text in UTF-8. File is deleted after read.
#   $5  model or '-', $6 effort or '-'; '-' means "no flag"
#
# bash is invoked with -l so ~/.bashrc / ~/.profile are sourced and PATH
# includes user-scoped install locations (e.g. ~/.npm-global/bin, ~/.local/bin)
# where `claude` typically lives.

set -e

WSPATH=$1
SESSION_FLAG=$2
SESSION_ID=$3
PROMPT_FILE=$4
MODEL=${5:--}
EFFORT=${6:--}
LOG=/mnt/c/Work/Switchboard/logs/spawn-wsl.log

if [ -z "$WSPATH" ] || [ -z "$SESSION_FLAG" ] || [ -z "$SESSION_ID" ] || [ -z "$PROMPT_FILE" ]; then
	echo "[$(date -u +%FT%TZ)] usage error: missing arg(s). got: WSPATH='$WSPATH' SESSION_FLAG='$SESSION_FLAG' SESSION_ID='$SESSION_ID' PROMPT_FILE='$PROMPT_FILE'" >> "$LOG"
	exit 1
fi

if [ ! -f "$PROMPT_FILE" ]; then
	echo "[$(date -u +%FT%TZ)] prompt file not found: $PROMPT_FILE" >> "$LOG"
	exit 1
fi

PROMPT=$(cat "$PROMPT_FILE")
rm -f "$PROMPT_FILE"

echo "[$(date -u +%FT%TZ)] start path='$WSPATH' session='$SESSION_ID' flag='$SESSION_FLAG' model='$MODEL' effort='$EFFORT' distro=${WSL_DISTRO_NAME:-?} claude=$(command -v claude || echo MISSING) PATH=$PATH" >> "$LOG" 2>&1

EXTRA_ARGS=()
if [ "$MODEL" != "-" ]; then EXTRA_ARGS+=(--model "$MODEL"); fi
if [ "$EFFORT" != "-" ]; then EXTRA_ARGS+=(--effort "$EFFORT"); fi

cd "$WSPATH" && claude "$PROMPT" "$SESSION_FLAG" "$SESSION_ID" --dangerously-skip-permissions "${EXTRA_ARGS[@]}"
EC=$?

echo "[$(date -u +%FT%TZ)] exit $EC" >> "$LOG"
exit $EC
