"""Build identity for the running service, reported on /healthz.

Tests, a commit, and a plugin update all leave the running process untouched,
so a service can be healthy and still predate the working tree. The source
fingerprint lets the smoke harness assert the process it is about to exercise
runs the code on disk. scripts/smoke/_smoke_lib.py carries a copy of
source_fingerprint (the harness imports nothing from server/), and
tests/test_build_info.py pins the two together.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path


def source_fingerprint(root: Path) -> str:
	"""sha256 over every *.py under root, in root-relative path order.

	Each file contributes its relative path and its bytes with CRLF normalized
	to LF, since this tree's line endings drift without any content change."""
	digest = hashlib.sha256()
	for rel, path in sorted((p.relative_to(root).as_posix(), p) for p in root.rglob("*.py")):
		data = path.read_bytes().replace(b"\r\n", b"\n")
		digest.update(f"{rel}\n{len(data)}\n".encode("utf-8"))
		digest.update(data)
	return digest.hexdigest()


def read_git_head(repo_root: Path) -> str | None:
	"""The commit sha HEAD points at, read from .git directly.

	Not via `git`: the service runs as LocalSystem, and git refuses a repo owned
	by another user ("dubious ownership") unless safe.directory is configured.

	None when HEAD cannot be resolved (no repo, a worktree's .git file, a
	dangling ref): this is an informational field, and the fingerprint is what
	freshness actually asserts on."""
	git_dir = repo_root / ".git"
	try:
		head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
		if not head.startswith("ref: "):
			return head
		ref = head.removeprefix("ref: ")
		loose = git_dir / ref
		if loose.is_file():
			return loose.read_text(encoding="utf-8").strip()
		for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
			sha, _, name = line.partition(" ")
			if name == ref:
				return sha
	except OSError:
		return None
	return None


def service_identity(repo_root: Path, source_root: Path, started_at: datetime) -> dict:
	"""The /healthz "service" block, captured once at startup."""
	return {
		"started_at": started_at.isoformat(timespec="seconds"),
		"source_fingerprint": source_fingerprint(source_root),
		"git_head": read_git_head(repo_root),
	}
