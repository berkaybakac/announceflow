"""StreamManager integration tests — real subprocess lifecycle."""
import time

from stream_manager import StreamManager


class TestStreamManagerIntegration:
    def test_start_stop_lifecycle(self):
        mgr = StreamManager(port=15800)
        assert not mgr.is_alive()

        assert mgr.start_receiver() is True
        assert mgr.is_alive() is True

        # Idempotent start
        assert mgr.start_receiver() is True

        assert mgr.stop_receiver() is True
        assert not mgr.is_alive()

        # Idempotent stop
        assert mgr.stop_receiver() is True

    def test_is_alive_detects_dead_process(self):
        mgr = StreamManager(port=15801)
        mgr.start_receiver()
        assert mgr.is_alive()

        # Kill externally
        mgr._process.kill()
        mgr._process.wait()
        time.sleep(0.1)

        assert not mgr.is_alive()
