"""Path canonicalization - display-only since the conversations redesign.

Routing keys are session_id / conversation_id; cwd is informational.
canonicalize_cwd normalizes raw cwd strings into a stable display form
(Windows, Git-Bash /c/..., and WSL-mount /mnt/c/... forms of the same
directory all map to one canonical string). No production path is wired
to it yet; whether display surfaces should route cwd labels through it
is a pending design decision, and the utility and its tests are kept
for that. Do not delete as dead code while that decision is open.
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
	"""Normalize raw cwd to the canonical display form used as a display label.

	Accepts both Windows-style paths (drive letter + colon, with backslash or
	forward-slash separators, plus Git-Bash-style /c/... and WSL-mount
	/mnt/c/...) and POSIX-style paths (absolute, starting with /). The WSL
	mount form is rewritten to its Windows equivalent so a WSL agent at
	/mnt/c/Work and a Windows agent at C:/Work produce the same display label
	(their cwds are the same physical directory). Other POSIX paths become
	distinct labels from Windows ones; cross-environment collab across
	separate filesystems requires both agents to pass the same string - by
	convention the Windows-style form - but that's a caller-side convention,
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
