import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from factory_monitor.inference import (
    LocalReviewError,
    ModelUnavailable,
    OllamaReviewer,
    YoloPersonDetector,
)


class _OllamaHandler(BaseHTTPRequestHandler):
    requests = 0
    last_body = None
    response_content = {"decision": "uncertain", "reason": "blur", "visible_evidence": ["frame is blurred"], "target_visible": False, "target_type": "unclear"}

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers["content-length"])
        body = json.loads(self.rfile.read(length))
        type(self).last_body = body
        assert self.path == "/api/chat"
        assert body["model"] == "qwen3-vl:2b-instruct"
        assert body["stream"] is False
        assert body["format"] == "json"
        payload = json.dumps({"message": {"content": json.dumps(type(self).response_content)}})
        encoded = payload.encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


@pytest.fixture
def ollama_server():
    _OllamaHandler.requests = 0
    _OllamaHandler.last_body = None
    _OllamaHandler.response_content = {
        "decision": "uncertain",
        "reason": "blur",
        "visible_evidence": ["frame is blurred"],
        "target_visible": False,
        "target_type": "unclear",
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def test_ollama_reviewer_accepts_loopback_and_parses_json(ollama_server):
    reviewer = OllamaReviewer(ollama_server, "qwen3-vl:2b-instruct", timeout_seconds=2)

    result = reviewer.review(np.zeros((32, 32, 3), dtype=np.uint8), deadline=time.monotonic() + 2)

    assert result["decision"] == "uncertain"
    assert result["reason"] == "blur"
    assert _OllamaHandler.requests == 1


def test_ollama_reviewer_rejects_non_loopback_endpoint():
    with pytest.raises(LocalReviewError, match="loopback"):
        OllamaReviewer("https://example.com", "qwen3-vl:2b-instruct", timeout_seconds=2)


def test_queue_wait_counts_toward_review_deadline(ollama_server):
    reviewer = OllamaReviewer(ollama_server, "qwen3-vl:2b-instruct", timeout_seconds=2)

    with pytest.raises(TimeoutError, match="queue deadline"):
        reviewer.review(np.zeros((8, 8, 3), dtype=np.uint8), deadline=time.monotonic() - 0.01)
    assert _OllamaHandler.requests == 0


def test_missing_yolo_model_is_a_visible_failure(tmp_path: Path):
    with pytest.raises(ModelUnavailable, match="model file"):
        YoloPersonDetector(tmp_path / "missing.pt", device="cpu")


def test_ollama_reviewer_sends_ordered_timestamped_frames_with_token_bound(ollama_server):
    reviewer = OllamaReviewer(ollama_server, "qwen3-vl:2b-instruct", timeout_seconds=2)
    frames = [
        {"timestamp": 100.0, "image": np.zeros((8, 8, 3), dtype=np.uint8)},
        {"timestamp": 101.5, "image": np.ones((8, 8, 3), dtype=np.uint8)},
    ]

    reviewer.review(
        frames,
        deadline=time.monotonic() + 2,
        context={"kind": "material_candidate", "reason": "entered_material_roi"},
    )

    body = _OllamaHandler.last_body
    assert len(body["messages"][0]["images"]) == 2
    assert "100.000" in body["messages"][0]["content"]
    assert "101.500" in body["messages"][0]["content"]
    assert "cable/wire bundle/coil" in body["messages"][0]["content"]
    assert "person alone" in body["messages"][0]["content"]
    assert body["options"]["num_predict"] == 256


def test_ollama_reviewer_rejects_malformed_or_unbounded_decision(ollama_server):
    _OllamaHandler.response_content = {"decision": "confirmed theft", "reason": "looks suspicious"}
    reviewer = OllamaReviewer(ollama_server, "qwen3-vl:2b-instruct", timeout_seconds=2)

    with pytest.raises(LocalReviewError, match="schema"):
        reviewer.review(np.zeros((8, 8, 3), dtype=np.uint8), deadline=time.monotonic() + 2)


def test_material_review_never_accepts_generic_bag_as_supported(ollama_server):
    _OllamaHandler.response_content = {
        "decision": "supported",
        "reason": "person carries a bag, not a cable",
        "visible_evidence": ["black bag", "no cable visible"],
        "target_visible": False,
        "target_type": "none",
    }
    reviewer = OllamaReviewer(ollama_server, "qwen3-vl:2b-instruct", timeout_seconds=2)

    with pytest.raises(LocalReviewError, match="material schema"):
        reviewer.review(
            np.zeros((8, 8, 3), dtype=np.uint8),
            deadline=time.monotonic() + 2,
            context={"kind": "material_candidate"},
        )
