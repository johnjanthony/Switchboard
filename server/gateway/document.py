from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import asyncio
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote

_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_DENYLIST_EXACT = frozenset({".env", "service-account.json", "credentials.json"})
_DENYLIST_GLOBS = ("*token*", "*secret*", "*.pem", "*.key", ".env*", "*.env")

# Extension allowlist (REV-004): send_document_human exists to deliver reports,
# logs, diffs, and images to the phone. Everything else - source, archives,
# binaries, HTML/SVG (active content), JSON (credentials shape), key material
# (id_rsa, *.p12, *.jks, ...), and extensionless files - is refused. The
# denylist above still runs first so known-secret names keep their specific
# rejection messages.
_ALLOWED_EXTENSIONS = frozenset({
	".md", ".markdown", ".txt", ".log", ".csv", ".tsv", ".diff", ".patch",
	".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
})

async def _sha256_hex(path: Path) -> str:
	def _do_sha256():
		h = hashlib.sha256()
		with path.open("rb") as f:
			for chunk in iter(lambda: f.read(65536), b""):
				h.update(chunk)
		return h.hexdigest()
	return await asyncio.to_thread(_do_sha256)

def _validate_path(path_str: str, cwd: Path | None = None) -> Path:
	"""Return the resolved Path if safe; raise ValueError otherwise."""
	p = Path(path_str)
	_cwd = (cwd or Path.cwd()).resolve()
	if p.is_absolute():
		resolved = p.resolve()
	else:
		resolved = (_cwd / p).resolve()
	# Containment is enforced for every input, absolute or relative: an absolute
	# path is only accepted if it still resolves to somewhere inside the project.
	# (Previously absolute paths skipped this check entirely, letting an agent
	# read any file on the machine.)
	try:
		resolved.relative_to(_cwd)
	except ValueError:
		raise ValueError(f"Path escapes project directory: {path_str}")

	if not resolved.exists():
		raise ValueError(f"File not found: {path_str}")
	if not resolved.is_file():
		raise ValueError(f"Not a file: {path_str}")

	size = resolved.stat().st_size
	if size > _MAX_DOCUMENT_BYTES:
		raise ValueError(f"File too large ({size} bytes, max {_MAX_DOCUMENT_BYTES})")

	_check_name(resolved.name)
	return resolved

def _check_name(name: str) -> None:
	"""Raise ValueError unless a file of this name may be shared."""
	name_lower = name.lower()
	if name_lower in _DENYLIST_EXACT:
		raise ValueError(f"File is on the deny list: {name}")
	for pattern in _DENYLIST_GLOBS:
		if fnmatch.fnmatch(name_lower, pattern):
			raise ValueError(
				f"File matches restricted pattern '{pattern}': {name}"
			)

	ext = PurePosixPath(name_lower).suffix
	if ext not in _ALLOWED_EXTENSIONS:
		allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
		raise ValueError(
			f"File type '{ext or '(no extension)'}' is not on the shareable allowlist ({allowed}): {name}"
		)

def _validate_content(content_path: str | None, content_base64: str) -> tuple[str, bytes]:
	"""Return (file name, bytes) for a file the switchboard mod read inside the
	session; raise ValueError otherwise. The mod has already held the path to
	the session's cwd, which this server cannot see (a WSL path)."""
	if not content_path:
		raise ValueError("content_base64 needs content_path")
	name = PurePosixPath(content_path).name
	_check_name(name)
	# Judged on the encoded length first, so an oversized payload is never decoded.
	if len(content_base64) > 4 * -(-_MAX_DOCUMENT_BYTES // 3):
		raise ValueError(f"File too large (over {_MAX_DOCUMENT_BYTES} bytes, max {_MAX_DOCUMENT_BYTES})")
	try:
		data = base64.b64decode(content_base64, validate=True)
	except binascii.Error:
		raise ValueError("content_base64 is not valid base64") from None
	if len(data) > _MAX_DOCUMENT_BYTES:
		raise ValueError(f"File too large ({len(data)} bytes, max {_MAX_DOCUMENT_BYTES})")
	return name, data


# mimetypes is incomplete/peculiar across platforms; pin the types we care about.
_CONTENT_TYPE_OVERRIDES = {
	".md": "text/markdown",
	".markdown": "text/markdown",
	".log": "text/plain",
	".yml": "text/yaml",
	".yaml": "text/yaml",
	".ts": "text/plain",
	".tsx": "text/plain",
	".kt": "text/plain",
	".kts": "text/plain",
	".toml": "text/plain",
}


def guess_content_type(filename: str) -> str:
	"""Best-effort MIME type for a filename. Falls back to octet-stream."""
	ext = Path(filename).suffix.lower()
	if ext in _CONTENT_TYPE_OVERRIDES:
		return _CONTENT_TYPE_OVERRIDES[ext]
	guessed, _ = mimetypes.guess_type(filename)
	return guessed or "application/octet-stream"


def _blob_path_from_url(url: str | None) -> str | None:
	"""Parse the storage blob path (documents/<uuid>/<file>) out of a stored GCS
	v4 signed URL of the form https://storage.googleapis.com/<bucket>/<object>?...

	This lets a document message written before the storage_path field existed
	still be downloaded via the Admin SDK, immune to signed-url expiry."""
	if not url:
		return None
	path = unquote(urlparse(url).path).lstrip("/")
	parts = path.split("/", 1)  # ["<bucket>", "documents/<uuid>/<file>"]
	if len(parts) != 2:
		return None
	rest = parts[1]
	return rest if rest.startswith("documents/") else None
