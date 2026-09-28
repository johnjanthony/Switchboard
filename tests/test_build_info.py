"""Tests for server/build_info.py - the build identity /healthz reports so the
smoke harness can tell a running service apart from the working tree."""

from datetime import datetime, timezone

from server.build_info import read_git_head, service_identity, source_fingerprint

_SHA = "0123456789abcdef0123456789abcdef01234567"


def _write(root, rel, data: bytes):
	path = root / rel
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(data)


def test_fingerprint_ignores_line_ending_style(tmp_path):
	"""The working tree drifts between CRLF and LF without any content change,
	so an ending flip after a restart must not read as a stale service."""
	lf, crlf = tmp_path / "lf", tmp_path / "crlf"
	_write(lf, "a.py", b"x = 1\ny = 2\n")
	_write(crlf, "a.py", b"x = 1\r\ny = 2\r\n")
	assert source_fingerprint(lf) == source_fingerprint(crlf)


def test_fingerprint_changes_with_content_path_and_file_set(tmp_path):
	"""Any edit, rename, addition, or deletion of a module must change it."""
	_write(tmp_path, "a.py", b"x = 1\n")
	_write(tmp_path, "pkg/b.py", b"y = 2\n")
	base = source_fingerprint(tmp_path)

	_write(tmp_path, "a.py", b"x = 2\n")
	edited = source_fingerprint(tmp_path)
	_write(tmp_path, "a.py", b"x = 1\n")
	assert source_fingerprint(tmp_path) == base

	(tmp_path / "pkg/b.py").rename(tmp_path / "pkg/c.py")
	renamed = source_fingerprint(tmp_path)
	(tmp_path / "pkg/c.py").rename(tmp_path / "pkg/b.py")

	_write(tmp_path, "new.py", b"")
	added = source_fingerprint(tmp_path)
	(tmp_path / "new.py").unlink()

	(tmp_path / "a.py").unlink()
	removed = source_fingerprint(tmp_path)

	assert len({base, edited, renamed, added, removed}) == 5


def test_fingerprint_ignores_non_python_files(tmp_path):
	"""Bytecode caches and other files next to the sources are not the code."""
	_write(tmp_path, "a.py", b"x = 1\n")
	base = source_fingerprint(tmp_path)
	_write(tmp_path, "__pycache__/a.cpython-314.pyc", b"\x00\x01")
	_write(tmp_path, "notes.txt", b"hello")
	assert source_fingerprint(tmp_path) == base


def test_git_head_resolves_a_loose_branch_ref(tmp_path):
	_write(tmp_path, ".git/HEAD", b"ref: refs/heads/develop\n")
	_write(tmp_path, ".git/refs/heads/develop", f"{_SHA}\n".encode())
	assert read_git_head(tmp_path) == _SHA


def test_git_head_reads_a_detached_head(tmp_path):
	_write(tmp_path, ".git/HEAD", f"{_SHA}\n".encode())
	assert read_git_head(tmp_path) == _SHA


def test_git_head_falls_back_to_packed_refs(tmp_path):
	_write(tmp_path, ".git/HEAD", b"ref: refs/heads/develop\n")
	other = "f" * 40
	_write(tmp_path, ".git/packed-refs", (
		"# pack-refs with: peeled fully-peeled sorted\n"
		f"{other} refs/heads/main\n"
		f"{_SHA} refs/heads/develop\n"
		f"^{other}\n"
	).encode())
	assert read_git_head(tmp_path) == _SHA


def test_git_head_is_none_when_unresolvable(tmp_path):
	"""An informational field: no repo, a worktree's .git file, or a ref that
	resolves nowhere reports unknown rather than failing service startup."""
	assert read_git_head(tmp_path / "no-repo") is None

	_write(tmp_path / "worktree", ".git", b"gitdir: C:/elsewhere/.git/worktrees/wt\n")
	assert read_git_head(tmp_path / "worktree") is None

	_write(tmp_path / "dangling", ".git/HEAD", b"ref: refs/heads/gone\n")
	assert read_git_head(tmp_path / "dangling") is None


def test_service_identity_reports_start_fingerprint_and_head(tmp_path):
	_write(tmp_path, ".git/HEAD", f"{_SHA}\n".encode())
	_write(tmp_path, "server/main.py", b"x = 1\n")
	started = datetime(2026, 9, 28, 14, 5, 6, tzinfo=timezone.utc)
	assert service_identity(tmp_path, tmp_path / "server", started) == {
		"started_at": "2026-09-28T14:05:06+00:00",
		"source_fingerprint": source_fingerprint(tmp_path / "server"),
		"git_head": _SHA,
	}
