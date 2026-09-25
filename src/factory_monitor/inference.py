"""Actual person detection/tracking and loopback-only visual review."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np


class ModelUnavailable(RuntimeError):
    pass


class LocalReviewError(RuntimeError):
    pass


def _resolve_device(device: str) -> str:
    if device not in {"cpu", "mps", "auto"}:
        return device
    if device == "cpu":
        return "cpu"
    try:
        import torch
    except ImportError as exc:
        if device == "mps":
            raise ModelUnavailable("torch with MPS support is unavailable") from exc
        return "cpu"
    if device == "auto" and torch.cuda.is_available():
        return "cuda"
    available = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    if device == "mps" and not available:
        raise ModelUnavailable("requested MPS device is unavailable")
    return "mps" if available else "cpu"


class YoloPersonDetector:
    """Ultralytics YOLO with one persistent ByteTrack state per camera."""

    def __init__(self, model_path: str | Path, *, device: str = "cpu", confidence: float = 0.35) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise ModelUnavailable(f"YOLO model file does not exist: {self.model_path}")
        if self.model_path.suffix.lower() not in {".pt", ".onnx", ".xml", ".mlpackage"}:
            raise ModelUnavailable(f"unsupported YOLO model format: {self.model_path.suffix}")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ModelUnavailable("ultralytics is not installed") from exc
        self._yolo_type = YOLO
        self.device = _resolve_device(device)
        self.confidence = float(confidence)
        self._model: Any | None = None
        self._trackers: dict[str, Any] = {}

    def detect(self, camera_id: str, image: np.ndarray) -> list[dict[str, Any]]:
        return self.detect_batch({camera_id: image})[camera_id]

    def reset(self, camera_id: str) -> None:
        """Discard one camera's temporal state after blindness or a layout change."""
        self._trackers.pop(camera_id, None)

    def _new_tracker(self) -> Any:
        from ultralytics.trackers.basetrack import BaseTrack
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import IterableSimpleNamespace, YAML
        from ultralytics.utils.checks import check_yaml

        config = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        config.device = self.device
        # BYTETracker.__init__ resets a process-wide counter. Preserve it so
        # adding or resetting one camera cannot reuse an active camera's ID.
        next_id = BaseTrack._count
        tracker = BYTETracker(args=config)
        BaseTrack._count = max(next_id, BaseTrack._count)
        return tracker

    def detect_batch(self, images: dict[str, np.ndarray]) -> dict[str, list[dict[str, Any]]]:
        """Run one prediction for the visible images, then track each camera separately."""
        if not images:
            return {}
        if self._model is None:
            try:
                self._model = self._yolo_type(str(self.model_path), task="detect")
            except Exception as exc:
                raise ModelUnavailable(f"could not load YOLO model: {exc}") from exc
        camera_ids = list(images)
        try:
            results = self._model.predict(
                source=[images[camera_id] for camera_id in camera_ids],
                classes=[0],
                conf=self.confidence,
                device=self.device,
                verbose=False,
            )
        except Exception as exc:
            raise ModelUnavailable(f"YOLO inference failed: {exc}") from exc
        if len(results) != len(camera_ids):
            raise ModelUnavailable("YOLO returned a different number of results than camera images")
        output: dict[str, list[dict[str, Any]]] = {}
        for camera_id, result in zip(camera_ids, results, strict=True):
            image = images[camera_id]
            tracker = self._trackers.get(camera_id)
            if tracker is None:
                tracker = self._new_tracker()
                self._trackers[camera_id] = tracker
            try:
                tracks = tracker.update(result.boxes.cpu().numpy(), image)
            except Exception as exc:
                raise ModelUnavailable(f"ByteTrack failed for {camera_id}: {exc}") from exc
            height, width = image.shape[:2]
            people: list[dict[str, Any]] = []
            for track in tracks:
                x1, y1, x2, y2 = (float(value) for value in track[:4])
                normalized = [
                    min(1.0, max(0.0, x1 / width)),
                    min(1.0, max(0.0, y1 / height)),
                    min(1.0, max(0.0, x2 / width)),
                    min(1.0, max(0.0, y2 / height)),
                ]
                if normalized[0] >= normalized[2] or normalized[1] >= normalized[3]:
                    continue
                people.append({"track_id": int(track[4]), "bbox": normalized, "confidence": float(track[5])})
            output[camera_id] = people
        return output


class OllamaReviewer:
    """Review a frame through Ollama on loopback with a queue-inclusive deadline."""

    def __init__(self, endpoint: str, model: str, *, timeout_seconds: float = 15) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalReviewError("Ollama endpoint must be loopback HTTP")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = float(timeout_seconds)

    def review(
        self,
        image: np.ndarray | list[dict[str, Any]],
        *,
        deadline: float,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        remaining = min(self.timeout_seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("review queue deadline expired before request")
        if isinstance(image, np.ndarray):
            frames = [{"timestamp": time.time(), "image": image}]
        else:
            frames = list(image)
        if not 1 <= len(frames) <= 6:
            raise LocalReviewError("review requires one to six ordered frames")
        encoded_images: list[str] = []
        timestamps: list[float] = []
        previous = float("-inf")
        for frame in frames:
            timestamp = float(frame["timestamp"])
            if timestamp < previous:
                raise LocalReviewError("review frames must be timestamp ordered")
            previous = timestamp
            ok, encoded = cv2.imencode(".jpg", frame["image"], [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise LocalReviewError("could not encode review frame")
            encoded_images.append(base64.b64encode(encoded.tobytes()).decode("ascii"))
            timestamps.append(timestamp)
            if time.monotonic() >= deadline:
                raise TimeoutError("review deadline expired while encoding frames")
        prompt = (
            "Review these ordered timestamped industrial monitoring frames. Return JSON only with "
            "decision (supported, dismissed, or uncertain), a non-empty reason, and visible_evidence "
            "as a list of only directly visible facts. "
            "Keep reason under 25 words and 180 characters. Include at most three short facts, "
            "each under 100 characters. Do not repeat. When evidence is insufficient, use uncertain. "
            "Never infer intent, theft, identity, or work ethic."
        )
        prompt += " Frame timestamps (epoch seconds): " + ", ".join(f"{value:.3f}" for value in timestamps) + "."
        if context:
            if context.get("kind") == "material_candidate":
                prompt += (
                    " For a material_candidate, supported requires a person visibly carrying or handling a "
                    "cable/wire bundle/coil in the relevant sequence. A person alone, generic bag, cart, or "
                    "proximity is not support. Also return target_visible as a boolean and target_type as one "
                    "of cable, wire, bundle, coil, none, or unclear. supported is permitted only when "
                    "target_visible is true and target_type is cable, wire, bundle, or coil. Do not infer "
                    "ownership, authorization, or intent."
                )
            elif context.get("kind") == "station_absence":
                prompt += (
                    " For station_absence, the deterministic rule supplied the configured timing threshold and "
                    "station_roi; check only whether that specified station region is visibly unoccupied in these "
                    "frames. Occlusion or an unclear region is uncertain. Do not infer work ethic or why anyone "
                    "is absent."
                )
            prompt += " Context: " + json.dumps(context, ensure_ascii=False)
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["supported", "dismissed", "uncertain"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 180},
                "visible_evidence": {"type": "array", "maxItems": 3,
                                     "items": {"type": "string", "minLength": 1, "maxLength": 100}},
            },
            "required": ["decision", "reason", "visible_evidence"],
            "additionalProperties": False,
        }
        if context and context.get("kind") == "material_candidate":
            schema["properties"].update({
                "target_visible": {"type": "boolean"},
                "target_type": {"type": "string", "enum": ["cable", "wire", "bundle", "coil", "none", "unclear"]},
            })
            schema["required"].extend(["target_visible", "target_type"])
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": encoded_images,
                }
            ],
            "options": {"temperature": 0, "num_predict": 256},
        }
        remaining = min(self.timeout_seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("review deadline expired before local request")
        try:
            # Ignore proxy environment variables: a local-only review must never
            # leave loopback, even on machines with a global HTTP proxy.
            with httpx.Client(trust_env=False, timeout=remaining) as client:
                response = client.post(f"{self.endpoint}/api/chat", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("local Ollama review timed out") from exc
        except httpx.HTTPError as exc:
            raise LocalReviewError(f"local Ollama request failed: {exc}") from exc
        if time.monotonic() >= deadline:
            raise TimeoutError("local Ollama response exceeded review deadline")
        try:
            outer = response.json()
            content = outer["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LocalReviewError("local Ollama returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise LocalReviewError("local Ollama response must be a JSON object")
        decision = result.get("decision")
        reason = result.get("reason")
        visible = result.get("visible_evidence")
        if (
            decision not in {"supported", "dismissed", "uncertain"}
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 180
            or not isinstance(visible, list)
            or len(visible) > 3
            or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in visible)
            or (decision == "supported" and not visible)
        ):
            raise LocalReviewError("local Ollama response failed the review schema")
        if context and context.get("kind") == "material_candidate":
            target_visible = result.get("target_visible")
            target_type = result.get("target_type")
            allowed_targets = {"cable", "wire", "bundle", "coil", "none", "unclear"}
            if not isinstance(target_visible, bool) or target_type not in allowed_targets:
                raise LocalReviewError("local Ollama response failed the material schema")
            if decision == "supported" and (not target_visible or target_type not in {"cable", "wire", "bundle", "coil"}):
                raise LocalReviewError("local Ollama response failed the material schema")
        return result
