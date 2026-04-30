from __future__ import annotations

import fnmatch
import hashlib
import asyncio
from pathlib import Path

_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_DENYLIST_EXACT = frozenset({".env", "service-account.json"})
_DENYLIST_GLOBS = ("*token*", "*secret*", "*.pem", "*.key", ".env*", "*.env")

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
	if p.is_absolute():
		resolved = p.resolve()
	else:
		_cwd = (cwd or Path.cwd()).resolve()
		resolved = (_cwd / p).resolve()
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

	name_lower = resolved.name.lower()
	if name_lower in _DENYLIST_EXACT:
		raise ValueError(f"File is on the deny list: {resolved.name}")
	for pattern in _DENYLIST_GLOBS:
		if fnmatch.fnmatch(name_lower, pattern):
			raise ValueError(
				f"File matches restricted pattern '{pattern}': {resolved.name}"
			)

	return resolved
