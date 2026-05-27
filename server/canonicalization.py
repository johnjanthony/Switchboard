"""Path canonicalization — display-only since the conversations redesign.

Routing keys are session_id / conversation_id; cwd is informational. The
canonicalize_cwd function still normalizes raw cwd strings into a stable
display form (so Page B shows a consistent label regardless of how the
agent passed the path: Windows / Git-Bash / POSIX / WSL `/mnt/<letter>/...`
all map to the same canonical form). to_firebase_key derives Firebase-safe
keys; surviving callers are limited to the Wear-compat /channels/<key>
projection and a few legacy slot-parsing paths in firebase.py.
"""

from __future__ import annotations

import os
import re
from pathlib import PureWindowsPath


class CanonicalizationError(ValueError):
	"""Raised when a path cannot be canonicalized."""


_GIT_BASH_PREFIX = re.compile(r"^/([a-zA-Z])/(.*)$")
_WSL_MOUNT_PREFIX = re.compile(r"^/mnt/([a-z])/(.*)$")


def canonicalize_cwd(raw: str) -> str:
	"""Normalize raw cwd to the canonical form used as a routing key.

	Accepts both Windows-style paths (drive letter + colon, with backslash or
	forward-slash separators, plus Git-Bash-style /c/... and WSL-mount
	/mnt/c/...) and POSIX-style paths (absolute, starting with /). The WSL
	mount form is rewritten to its Windows equivalent so a WSL agent at
	/mnt/c/Work and a Windows agent at C:/Work share a routing key (their
	cwds are the same physical directory). Other POSIX paths become opaque
	routing keys distinct from Windows ones; cross-environment collab across
	separate filesystems requires both agents to pass the same string — by
	convention the Windows-style form — but that's a caller-side convention,
	not enforced here.

	Rules (Windows path):
	1. Reject empty / non-absolute / syntactically invalid paths.
	2. Convert WSL-mount /mnt/c/... and Git-Bash-style /c/... to c:/...
	3. Backslashes -> forward slashes.
	4. Lowercase drive letter (Windows is case-insensitive).
	5. Resolve . and .. segments.
	6. Strip trailing slash.

	Rules (POSIX path):
	1. Must start with /.
	2. Preserve case (POSIX is case-sensitive).
	3. Normalize . and .. segments.
	4. Strip trailing slash.
	5. Backslashes normalized to forward slashes if present (defensive; unusual on POSIX).

	Returns: 'c:/work/switchboard' or '/home/janthony/work/rpdm' style string.
	"""
	if not raw or not isinstance(raw, str):
		raise CanonicalizationError(f"cwd must be a non-empty string, got: {raw!r}")

	wsl = _WSL_MOUNT_PREFIX.match(raw)
	if wsl:
		raw = f"{wsl.group(1)}:/{wsl.group(2)}"

	gb = _GIT_BASH_PREFIX.match(raw)
	if gb:
		raw = f"{gb.group(1)}:/{gb.group(2)}"

	# Windows-style: drive letter at position 1.
	if len(raw) >= 2 and raw[1] == ":":
		try:
			win = PureWindowsPath(raw)
		except (ValueError, TypeError) as exc:
			raise CanonicalizationError(f"invalid path syntax: {raw!r}") from exc

		normalized = os.path.normpath(str(win))
		forward = normalized.replace("\\", "/").lower()

		if len(forward) > 3 and forward.endswith("/"):
			forward = forward.rstrip("/")

		return forward

	# POSIX-style: absolute path starting with /.
	if raw.startswith("/"):
		normalized = os.path.normpath(raw).replace("\\", "/")
		if len(normalized) > 1 and normalized.endswith("/"):
			normalized = normalized.rstrip("/")
		return normalized

	raise CanonicalizationError(
		f"cwd must be absolute (Windows drive letter or POSIX root), got: {raw!r}"
	)


def to_firebase_key(canonical_cwd: str) -> str:
	"""Convert canonical cwd to Firebase-safe key by flattening slashes.

	Each existing underscore is escaped to four underscores first; then each
	slash becomes two underscores. The decoder distinguishes the two by
	preferring the four-underscore match.

	Examples:
	    c:/work/foo   -> c:__work__foo
	    c:/work_foo   -> c:__work____foo
	    c:/work__foo  -> c:__work________foo
	"""
	escaped = canonical_cwd.replace("_", "____")
	return escaped.replace("/", "__")


def from_firebase_key(key: str) -> str:
	"""Reverse to_firebase_key; recovers canonical cwd from Firebase key.

	Walks the string left-to-right so ____ (literal _) is decoded before
	__ (slash) and they cannot be confused with overlapping replacements.
	"""
	result = []
	i = 0
	while i < len(key):
		if key[i:i + 4] == "____":
			result.append("_")
			i += 4
		elif key[i:i + 2] == "__":
			result.append("/")
			i += 2
		else:
			result.append(key[i])
			i += 1
	return "".join(result)
