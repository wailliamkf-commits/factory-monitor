import multiprocessing as mp
import queue
import time
from typing import Any

from factory_monitor.config import default_config
from factory_monitor.runtime import _capture_process
import factory_monitor.runtime as runtime_module


class _FakeQueue:
    def __init__(self) -> None:
        self.values: list[dict[str, Any]] = []
        self.cancel_count = 0

    def put(self, value: dict[str, Any], *args, **kwargs) -> None:
        self.values.append(value)

    def cancel_join_thread(self) -> None:
        self.cancel_count += 1


class _FakeStopEvent:
    def __init__(self, stopped: bool) -> None:
        self.stopped = stopped

    def is_set(self) -> bool:
        return self.stopped


class _ImmediateEndCapture:
    def __init__(self) -> None:
        self.closed = False

    def read(self):
        raise StopIteration

    def close(self) -> None:
        self.closed = True


def _message_until(messages: mp.Queue, predicate, timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if predicate(message):
            return message
    return None


def test_capture_closes_durable_frame_streams_before_it_exits():
    """Evidence/detection frames stay drainable; only preview may be discarded."""
    context = mp.get_context("spawn")
    detection_frames = context.Queue(maxsize=1)
    evidence_frames = context.Queue(maxsize=10)
    preview_frames = context.Queue(maxsize=1)
    messages = context.Queue(maxsize=20)
    stop_event = context.Event()
    frame_queues = [detection_frames, evidence_frames, preview_frames]
    all_queues = [*frame_queues, messages]
    config = default_config()
    config["detection"]["fps"] = 30
    process = context.Process(
        target=_capture_process,
        args=(
            "demo",
            config,
            None,
            detection_frames,
            evidence_frames,
            preview_frames,
            messages,
            stop_event,
        ),
    )

    try:
        process.start()
        running = _message_until(
            messages,
            lambda item: item.get("_kind") == "worker" and item.get("state") == "running",
            5,
        )
        assert running is not None
        queue_pressure = _message_until(messages, lambda item: item.get("_kind") == "stats", 3)
        assert queue_pressure is not None

        stop_event.set()
        durable_streams_closed = {"detection_frames": False, "evidence_frames": False}
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not all(durable_streams_closed.values()):
            for frame_queue in (detection_frames, evidence_frames):
                try:
                    packet = frame_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                stream = packet.get("stream") if isinstance(packet, dict) else None
                if packet.get("_protocol") == "eos" and stream in durable_streams_closed:
                    durable_streams_closed[stream] = True
        stopped = _message_until(
            messages,
            lambda item: item.get("_kind") == "worker" and item.get("state") == "drained",
            3,
        )
        assert stopped is not None
        assert all(durable_streams_closed.values())

        process.join(timeout=1.5)
        assert not process.is_alive(), "capture announced stopped but remained blocked flushing durable frames"
        assert process.exitcode == 0
    finally:
        stop_event.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        for item in all_queues:
            item.cancel_join_thread()
            item.close()


def test_capture_discards_only_frame_feeders_and_only_for_requested_stop(monkeypatch):
    config = default_config()

    def run(stopped: bool):
        source = _ImmediateEndCapture()
        frames = [_FakeQueue(), _FakeQueue(), _FakeQueue()]
        messages = _FakeQueue()
        monkeypatch.setattr(runtime_module, "open_capture", lambda *args: source)

        _capture_process(
            "demo",
            config,
            None,
            frames[0],
            frames[1],
            frames[2],
            messages,
            _FakeStopEvent(stopped),
        )
        return source, frames, messages

    stopped_source, stopped_frames, stopped_messages = run(stopped=True)
    ended_source, ended_frames, ended_messages = run(stopped=False)

    assert stopped_source.closed is True
    assert [item.cancel_count for item in stopped_frames] == [0, 0, 1]
    assert stopped_messages.cancel_count == 0
    assert [item["state"] for item in stopped_messages.values if item.get("_kind") == "worker"] == ["running", "drained"]
    assert stopped_frames[0].values[-1] == {"_protocol": "eos", "stream": "detection_frames"}
    assert stopped_frames[1].values[-1] == {"_protocol": "eos", "stream": "evidence_frames"}

    assert ended_source.closed is True
    assert [item.cancel_count for item in ended_frames] == [0, 0, 1]
    assert ended_messages.cancel_count == 0
    assert [item["state"] for item in ended_messages.values if item.get("_kind") == "worker"] == ["running", "ended", "drained"]
    assert ended_frames[0].values[-1] == {"_protocol": "eos", "stream": "detection_frames"}
    assert ended_frames[1].values[-1] == {"_protocol": "eos", "stream": "evidence_frames"}
