from starlette.applications import Starlette
from starlette.testclient import TestClient


class FakeDocBackend:
	def __init__(self, data=b"# Hi", filename="doc.md", error=None):
		self._data, self._filename, self._error = data, filename, error

	async def read_document(self, conv, msg):
		if self._error is not None:
			raise self._error
		return self._data, self._filename


def _app(backend):
	from server.main import _build_document_route
	app = Starlette()
	app.add_route("/document", _build_document_route(backend), methods=["GET"])
	return app


def test_document_streams_bytes_with_content_type():
	with TestClient(_app(FakeDocBackend(b"# Title", "report.md"))) as c:
		r = c.get("/document", params={"conv": "conv-1", "msg": "m-1"})
		assert r.status_code == 200
		assert r.content == b"# Title"
		assert r.headers["content-type"].startswith("text/markdown")
		assert "inline" in r.headers["content-disposition"]


def test_document_download_param_sets_attachment():
	with TestClient(_app(FakeDocBackend(b"x", "report.md"))) as c:
		r = c.get("/document", params={"conv": "conv-1", "msg": "m-1", "download": "1"})
		assert r.status_code == 200
		assert "attachment" in r.headers["content-disposition"]


def test_document_missing_params_returns_400():
	with TestClient(_app(FakeDocBackend())) as c:
		assert c.get("/document", params={"conv": "conv-1"}).status_code == 400
		assert c.get("/document").status_code == 400


def test_document_not_found_returns_404():
	with TestClient(_app(FakeDocBackend(error=LookupError("nope")))) as c:
		assert c.get("/document", params={"conv": "c", "msg": "m"}).status_code == 404


def test_document_download_failure_returns_502():
	with TestClient(_app(FakeDocBackend(error=RuntimeError("storage down")))) as c:
		assert c.get("/document", params={"conv": "c", "msg": "m"}).status_code == 502
