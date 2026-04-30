"""Ad-hoc Firebase RTDB query helper.

Reads FIREBASE_SERVICE_ACCOUNT_JSON and FIREBASE_DATABASE_URL from the process
environment (same vars the server uses) and dumps the requested ref path as
indented JSON to stdout.

Usage:
	python scripts/firebase_query.py <path> [<path>...]
	python scripts/firebase_query.py channels/c:__work
	python scripts/firebase_query.py global_settings/away_mode responses

Add `--shallow` to fetch only top-level keys (useful for huge nodes).

Read-only. Never writes or deletes.
"""

from __future__ import annotations

import json
import os
import sys

import firebase_admin
from firebase_admin import credentials, db


def main(argv: list[str]) -> int:
	if not argv:
		print(__doc__, file=sys.stderr)
		return 1

	shallow = False
	paths: list[str] = []
	for arg in argv:
		if arg == "--shallow":
			shallow = True
		else:
			paths.append(arg)

	if not paths:
		print("error: at least one path required", file=sys.stderr)
		return 1

	cred = credentials.Certificate(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"])
	firebase_admin.initialize_app(cred, {"databaseURL": os.environ["FIREBASE_DATABASE_URL"]})

	for path in paths:
		print(f"=== {path} ===")
		ref = db.reference(path)
		value = ref.get(shallow=True) if shallow else ref.get()
		print(json.dumps(value, indent=2, default=str, sort_keys=True))
		print()

	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
