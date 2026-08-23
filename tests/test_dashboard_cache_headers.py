"""Operator is a zero-build ESM app served straight off disk, so a browser
holding month-old modules looks identical to a live one: the Firebase data keeps
streaming while the JavaScript rendering it is stale. Starlette's StaticFiles
sends ETag/Last-Modified but no Cache-Control, which leaves reuse to heuristic
freshness. App code is therefore pinned to no-cache (store it, but revalidate
every load); vendor/ bundles are pinned by version and cached hard.

Cache-Control is a RESPONSE header and cannot influence either auth path: the
Bearer gate reads the Authorization REQUEST header, and Firebase's Google
sign-in lives in IndexedDB/localStorage, which HTTP cache directives cannot
reach.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from server.main import _CachePolicyStaticFiles

APP_CACHE_CONTROL = "no-cache"


@pytest.fixture
def dashboard_dir(tmp_path: Path) -> Path:
	(tmp_path / "vendor").mkdir()
	(tmp_path / "index.html").write_text("<p>shell</p>", encoding="utf-8")
	(tmp_path / "derive.js").write_text("export const x = 1;\n", encoding="utf-8")
	(tmp_path / "styles.css").write_text("body { color: red; }\n", encoding="utf-8")
	(tmp_path / "vendor" / "markdown-it.bundle.js").write_text("export const md = 1;\n", encoding="utf-8")
	return tmp_path


def _client(dashboard_dir: Path) -> httpx.AsyncClient:
	app = Starlette()
	app.mount("/dashboard", _CachePolicyStaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")
	return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/dashboard/derive.js", "/dashboard/styles.css", "/dashboard/index.html"])
async def test_app_assets_must_revalidate(dashboard_dir: Path, path: str):
	async with _client(dashboard_dir) as client:
		resp = await client.get(path)
	assert resp.status_code == 200
	assert resp.headers["cache-control"] == APP_CACHE_CONTROL


@pytest.mark.asyncio
async def test_vendor_bundles_are_cached_hard(dashboard_dir: Path):
	"""Pinned by version and never edited in place, so revalidating ~300 KB of
	markdown-it + highlight.js on every load buys nothing."""
	async with _client(dashboard_dir) as client:
		resp = await client.get("/dashboard/vendor/markdown-it.bundle.js")
	assert resp.status_code == 200
	cc = resp.headers["cache-control"]
	assert "immutable" in cc and "max-age=" in cc
	assert cc != APP_CACHE_CONTROL


@pytest.mark.asyncio
async def test_directory_index_also_revalidates(dashboard_dir: Path):
	"""html=True serves index.html for the bare /dashboard/ URL through a
	separate branch of get_response; the shell must not be the one stale file."""
	async with _client(dashboard_dir) as client:
		resp = await client.get("/dashboard/")
	assert resp.status_code == 200
	assert resp.headers["cache-control"] == APP_CACHE_CONTROL


@pytest.mark.asyncio
async def test_cache_control_survives_a_304(dashboard_dir: Path):
	"""Starlette builds a 304 by filtering headers down to NOT_MODIFIED_HEADERS.
	If the directive were attached after that filter ran, every revalidation
	would answer without it and the browser would fall back to heuristics."""
	async with _client(dashboard_dir) as client:
		first = await client.get("/dashboard/derive.js")
		second = await client.get("/dashboard/derive.js", headers={"If-None-Match": first.headers["etag"]})
	assert second.status_code == 304
	assert second.headers["cache-control"] == APP_CACHE_CONTROL
