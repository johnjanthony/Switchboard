"""The service stop flag, and the uvicorn server that raises it the moment a
stop is asked for."""

import signal

import uvicorn

from server.main import SwitchboardServer
from server.service_stop import ServiceStop


class _Mono:
	def __init__(self) -> None:
		self.t = 100.0

	def __call__(self) -> float:
		return self.t


def _server(stop: ServiceStop) -> SwitchboardServer:
	return SwitchboardServer(uvicorn.Config(app=lambda scope, receive, send: None), stop)


def test_a_fresh_flag_is_down_with_no_age():
	stop = ServiceStop()
	assert stop.requested is False
	assert stop.age_ms() is None


def test_raising_keeps_the_first_time():
	mono = _Mono()
	stop = ServiceStop(mono=mono)
	stop.raise_()
	mono.t += 0.5
	stop.raise_()
	mono.t += 0.25
	assert stop.requested is True
	assert stop.age_ms() == 750


def test_the_flag_is_up_before_uvicorn_handles_the_stop_signal(monkeypatch):
	stop = ServiceStop()
	seen = []
	monkeypatch.setattr(uvicorn.Server, "handle_exit", lambda self, sig, frame: seen.append(stop.requested))
	_server(stop).handle_exit(signal.SIGINT, None)
	assert seen == [True]


def test_the_stop_signal_still_asks_uvicorn_to_exit():
	stop = ServiceStop()
	server = _server(stop)
	server.handle_exit(signal.SIGINT, None)
	assert stop.requested is True
	assert server.should_exit is True


def test_request_stop_raises_the_flag_and_asks_uvicorn_to_exit():
	stop = ServiceStop()
	server = _server(stop)
	server.request_stop()
	assert stop.requested is True
	assert server.should_exit is True
