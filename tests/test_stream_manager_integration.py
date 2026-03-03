"""StreamManager integration tests — real subprocess lifecycle."""
import socket
import time

import pytest

from stream_manager import StreamManager


def _get_free_udp_port() -> int:
    """Return an ephemeral UDP port for test isolation."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class TestStreamManagerIntegration:
    def test_start_stop_lifecycle(self):
        mgr = StreamManager(port=_get_free_udp_port())
        try:
            assert not mgr.is_alive()

            assert mgr.start_receiver() is True
            assert mgr.is_alive() is True

            # Idempotent start
            assert mgr.start_receiver() is True

            assert mgr.stop_receiver() is True
            assert not mgr.is_alive()

            # Idempotent stop
            assert mgr.stop_receiver() is True
        finally:
            mgr.stop_receiver()

    def test_is_alive_detects_dead_process(self):
        mgr = StreamManager(port=_get_free_udp_port())
        try:
            if not mgr.start_receiver():
                pytest.skip(
                    "Stream receiver could not start in this environment "
                    "(likely sandbox/network restriction)"
                )
            assert mgr.is_alive()

            # Kill externally
            process = mgr._process
            assert process is not None
            process.kill()
            process.wait()
            time.sleep(0.1)

            assert not mgr.is_alive()
        finally:
            mgr.stop_receiver()
